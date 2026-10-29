"""A Director script speaks itself with an installed speech engine.

The bridge must not reimplement speech: it decides the speakers, the prompt and
the request shape, and leaves the model's own defaults and the pipeline's child-job
runner to do the work. These tests pin the decisions and the engine choice.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import tts_episode as tts  # noqa: E402


class FakeWgp:
    """The two model-registry calls the engine choice needs."""

    settings_version = 2.52

    def __init__(self, models: dict[str, dict], installed: set[str]):
        self.displayed_model_types = list(models)
        self._models = models
        self._installed = installed

    def get_model_def(self, model_type: str):
        return self._models.get(model_type)

    def get_model_download_confirmation(self, model_type: str):
        return None if model_type in self._installed else {"message": "download"}


def _speech_def(voices: int = 2, **overrides):
    model_def = {
        "audio_only": True,
        "returns_audio": True,
        "max_voice_count": voices,
    }
    model_def.update(overrides)
    return model_def


class SpokenRowsTests(unittest.TestCase):
    def test_only_spoken_rows_survive(self):
        rows = tts.spoken_rows([
            {"speaker": "BELA", "text": "  Hola.  "},
            {"speaker": "BELA", "text": "   "},
            {"text": "no speaker"},
            "not a row",
            {"speaker": "ROMAN", "text": "Adios."},
        ])

        self.assertEqual(
            [row["text"] for row in rows],
            ["Hola.", "no speaker", "Adios."],
        )

    def test_video_directives_are_removed_before_speech(self):
        # H3 reads `<d>[Spanish]` as a stage direction; a speech engine would
        # pronounce it, so the tag is dropped from the spoken text only.
        rows = tts.spoken_rows([
            {"speaker": "VALERIA", "text": "<d>[Spanish] La dignidad no se negocia.</d>"},
            {"speaker": "RICARDO", "text": "[Spanish] ¿Y qué propones?"},
        ])

        self.assertEqual(rows[0]["text"], "La dignidad no se negocia.")
        self.assertEqual(rows[1]["text"], "¿Y qué propones?")


class SpeakerSlotTests(unittest.TestCase):
    def test_slots_follow_first_use_order(self):
        rows = tts.spoken_rows([
            {"speaker": "ROMAN", "text": "Uno."},
            {"speaker": "BELA", "text": "Dos."},
            {"speaker": "ROMAN", "text": "Tres."},
        ])

        self.assertEqual(tts.speaker_slots(rows), {"ROMAN": 1, "BELA": 2})

    def test_the_prompt_uses_the_numbered_slot_convention(self):
        rows = tts.spoken_rows([
            {"speaker": "ROMAN", "text": "Uno."},
            {"speaker": "BELA", "text": "Dos."},
            {"speaker": "ROMAN", "text": "Tres."},
        ])
        prompt, paths = tts.build_prompt(rows, 2, {"ROMAN": "/v/roman.wav", "BELA": "/v/bela.wav"})

        self.assertEqual(
            prompt,
            "Speaker 1: Uno.\nSpeaker 2: Dos.\nSpeaker 1: Tres.",
        )
        self.assertEqual(paths, ["/v/roman.wav", "/v/bela.wav"])

    def test_a_cast_larger_than_the_engine_keeps_its_words(self):
        # Two slots, three speakers: the third shares the last slot rather than
        # losing the line or inventing a slot the model does not have.
        rows = tts.spoken_rows([
            {"speaker": "A", "text": "one"},
            {"speaker": "B", "text": "two"},
            {"speaker": "C", "text": "three"},
        ])
        prompt, paths = tts.build_prompt(rows, 2, {"A": "/v/a.wav", "B": "/v/b.wav", "C": "/v/c.wav"})

        self.assertEqual(len(paths), 2)
        self.assertEqual(prompt.splitlines()[-1], "Speaker 2: three")

    def test_a_speaker_without_a_voice_shares_the_last_slot(self):
        rows = tts.spoken_rows([
            {"speaker": "A", "text": "one"},
            {"speaker": "B", "text": "two"},
        ])
        prompt, paths = tts.build_prompt(rows, 2, {"A": "/v/a.wav"})

        self.assertEqual(paths, ["/v/a.wav"])
        self.assertEqual(prompt.splitlines()[1], "Speaker 1: two")

    def test_no_voice_reference_is_refused(self):
        # Measured on fish_drama3: a two-speaker prompt with no references
        # generated one partial line, and a bigger budget produced 0.046 s.
        rows = tts.spoken_rows([{"speaker": "A", "text": "one"}])
        with self.assertRaises(ValueError) as caught:
            tts.build_prompt(rows, 2, {})
        self.assertIn("voice reference", str(caught.exception))

    def test_two_speakers_sharing_one_voice_share_one_slot(self):
        # One saved character voicing both hosts must not spend two slots on the
        # same reference.
        rows = tts.spoken_rows([
            {"speaker": "A", "text": "one"},
            {"speaker": "B", "text": "two"},
        ])
        prompt, paths = tts.build_prompt(rows, 2, {"A": "/v/same.wav", "B": "/v/same.wav"})

        self.assertEqual(paths, ["/v/same.wav"])
        self.assertEqual(prompt, "Speaker 1: one\nSpeaker 1: two")


class EngineChoiceTests(unittest.TestCase):
    MODELS = {
        "fish_drama3": _speech_def(3, speech_auto_duration=True, speech_longform=True),
        "kugelaudio_0_open": _speech_def(6),
        "chatterbox": _speech_def(1),
        "some_video_model": {"returns_audio": False, "audio_only": False},
    }

    def test_a_long_form_multi_voice_engine_wins(self):
        wgp = FakeWgp(self.MODELS, {"fish_drama3", "kugelaudio_0_open"})
        model_type, model_def = tts.resolve_engine(wgp)

        self.assertEqual(model_type, "fish_drama3")
        self.assertEqual(model_def["max_voice_count"], 3)

    def test_an_engine_with_a_recorded_working_request_wins(self):
        # Model defaults alone are not a working speech configuration; the engine
        # this workspace has actually spoken with is tried first.
        wgp = FakeWgp(self.MODELS, {"fish_drama3", "kugelaudio_0_open"})
        model_type, _ = tts.resolve_engine(
            wgp, has_baseline=lambda candidate: candidate == "kugelaudio_0_open",
        )

        self.assertEqual(model_type, "kugelaudio_0_open")

    def test_a_single_speaker_engine_is_never_chosen(self):
        # Chatterbox is installed and first alphabetically, but it cannot voice
        # two hosts, so it must not be picked for a dialogue script.
        wgp = FakeWgp({"chatterbox": _speech_def(1)}, {"chatterbox"})
        with self.assertRaises(ValueError) as caught:
            tts.resolve_engine(wgp)
        self.assertIn("two speakers", str(caught.exception))

    def test_an_uninstalled_engine_is_skipped(self):
        wgp = FakeWgp(self.MODELS, {"kugelaudio_0_open"})
        model_type, _ = tts.resolve_engine(wgp)
        self.assertEqual(model_type, "kugelaudio_0_open")

    def test_an_explicit_request_is_honoured_or_refused(self):
        wgp = FakeWgp(self.MODELS, {"fish_drama3", "kugelaudio_0_open"})
        self.assertEqual(tts.resolve_engine(wgp, "kugelaudio_0_open")[0], "kugelaudio_0_open")
        with self.assertRaises(ValueError):
            tts.resolve_engine(wgp, "chatterbox")


class BaselineTests(unittest.TestCase):
    def test_the_newest_working_request_for_the_model_is_reused(self):
        import json
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp())
        studio = {"model_type": "fish_drama3", "_audio_sub_mode": "speech",
                  "_tts_character_id1": "abc", "_tts_voice_filename1": "one.wav"}
        (folder / "old.meta.json").write_text(json.dumps({
            "params": {**studio, "top_k": 50},
        }), encoding="utf-8")
        (folder / "newer.meta.json").write_text(json.dumps({
            "params": {**studio, "top_k": 40},
        }), encoding="utf-8")
        # A request this module made is never used as a baseline itself.
        (folder / "ours.meta.json").write_text(json.dumps({
            "params": {
                "model_type": "fish_drama3", "_audio_sub_mode": "speech",
                tts.BASELINE_SKIP_MARKER: True, "top_k": 0,
            },
        }), encoding="utf-8")
        # A request with reference paths but no voice bindings was not built by
        # the Studio, so it is not evidence that these settings speak.
        (folder / "script.meta.json").write_text(json.dumps({
            "params": {"model_type": "fish_drama3", "_audio_sub_mode": "speech",
                       "audio_guide": "/a.wav", "audio_guide2": "/b.wav"},
        }), encoding="utf-8")
        (folder / "song.meta.json").write_text(json.dumps({
            "params": {"model_type": "fish_drama3", "_audio_sub_mode": "music"},
        }), encoding="utf-8")

        baseline = tts.speech_baseline(folder, "fish_drama3")
        self.assertEqual(baseline["top_k"], 40)
        self.assertIsNone(tts.speech_baseline(folder, "kugelaudio_0_open"))

    def test_previous_speakers_and_references_are_stripped(self):
        clean = tts.without_voice_bindings({
            "top_k": 50,
            "audio_guide": "/old/one.wav",
            "audio_guide2": "/old/two.wav",
            "alt_prompt": "old reference transcript",
            "_tts_voice_count": 2,
            "_tts_character_id1": "abc",
            "voice_clone_refs": ["x"],
        })

        self.assertEqual(clean, {"top_k": 50})

    def test_the_built_request_is_marked_so_it_cannot_become_a_baseline(self):
        params = tts.build_params(
            {"top_k": 50}, "fish_drama3", _speech_def(3), "Speaker 1: hola", ["/v/a.wav"],
        )
        self.assertTrue(params[tts.BASELINE_SKIP_MARKER])
        self.assertEqual(params["top_k"], 50)


class SpeechParamsTests(unittest.TestCase):
    def test_the_request_is_a_speech_request_built_on_model_defaults(self):
        params = tts.build_params(
            {"resolution": "auto", "seed": -1},
            "fish_drama3",
            _speech_def(3, duration_slider={"default": 0}),
            "Speaker 1: Hola.",
            ["/v/bela.wav"],
            settings_version=2.52,
        )

        self.assertEqual(params["model_type"], "fish_drama3")
        self.assertEqual(params["generation_mode"], "audio")
        self.assertEqual(params["_audio_sub_mode"], "speech")
        self.assertEqual(params["prompt"], "Speaker 1: Hola.")
        self.assertEqual(params["_tts_original_prompt"], "Speaker 1: Hola.")
        self.assertEqual(params["_tts_voice_count"], 1)
        # The sampling values of the base request survive: they are the ones that
        # actually speak, and the model defaults' own values are not.
        self.assertEqual(params["seed"], -1)

    def test_the_voice_mode_comes_from_what_the_model_declares(self):
        # fish_drama3 publishes ["", "A", "AB", "ABD"]; two references must be AB.
        fish_def = _speech_def(
            3,
            audio_prompt_type_sources={
                "selection": ["", "A", "AB", "ABD"],
                "default": "",
            },
        )
        self.assertEqual(tts.voice_mode(fish_def, 0), "")
        self.assertEqual(tts.voice_mode(fish_def, 1), "A")
        self.assertEqual(tts.voice_mode(fish_def, 2), "AB")
        self.assertEqual(tts.voice_mode(fish_def, 3), "ABD")

    def test_the_voice_references_travel_with_their_slot(self):
        params = tts.build_params(
            {},
            "fish_drama3",
            _speech_def(3, audio_prompt_type_sources={"selection": ["", "A", "AB", "ABD"]}),
            "Speaker 1: a\nSpeaker 2: b",
            ["/v/one.wav", "/v/two.wav"],
        )

        self.assertEqual(params["audio_guide"], "/v/one.wav")
        self.assertEqual(params["audio_guide2"], "/v/two.wav")
        self.assertEqual(params["audio_prompt_type"], "AB")
        self.assertEqual(params["_tts_voice_count"], 2)

    def test_the_duration_is_the_models_own_value(self):
        # Every working fish_drama3 dialogue generation in this workspace sent 0
        # here (the model's automatic budget). A positive value made the language
        # model emit end-of-speech on its first frame and save a near-empty file.
        fish_def = _speech_def(3, duration_is_maximum=True, duration_slider={"default": 0})
        params = tts.build_params({}, "fish_drama3", fish_def, "Speaker 1: Hola.", ["/v/a.wav"])
        self.assertEqual(params["duration_seconds"], 0)

    def test_an_engine_with_its_own_slider_keeps_the_models_value(self):
        scenema = _speech_def(2, duration_slider={"default": 120})
        params = tts.build_params({}, "scenema_audio", scenema, "Speaker 1: text", ["/v/a.wav"])
        self.assertEqual(params["duration_seconds"], 120)

    def test_an_engine_without_a_slider_still_gets_a_duration(self):
        params = tts.build_params(
            {}, "kugelaudio_0_open", _speech_def(6), "word " * 280, ["/v/a.wav"],
        )
        self.assertGreaterEqual(params["duration_seconds"], 100)
        self.assertLess(params["duration_seconds"], 3600)

    def test_a_text_derived_budget_grows_with_the_script(self):
        fish_def = _speech_def(3, duration_is_maximum=True)
        short = tts.budget_seconds("Speaker 1: Hola.", fish_def)
        long_text = "\n".join(["Speaker 1: word " * 40] * 5)
        self.assertGreaterEqual(short, 30)
        self.assertGreater(tts.budget_seconds(long_text, fish_def), short)

    def test_an_estimated_duration_is_never_zero(self):
        self.assertGreaterEqual(tts.estimate_seconds("Hola."), 10.0)
        self.assertGreater(tts.estimate_seconds("word " * 280), tts.estimate_seconds("word " * 28))


class EndpointWiringTests(unittest.TestCase):
    def test_the_endpoint_runs_the_shared_child_job_runner(self):
        launch = (APP / "launch.py").read_text(encoding="utf-8")

        self.assertIn('@api.post("/api/v1/audio/tts-episode")', launch)
        self.assertIn("tts.resolve_engine(", launch)
        # It must reuse the pipeline's existing runner instead of reimplementing
        # a generation job.
        self.assertIn("director_pipeline._submit_and_wait(", launch)
        # Model defaults are not a working speech configuration; a recorded
        # request from this workspace is preferred when there is one.
        self.assertIn("baseline = tts.speech_baseline(out_dir, model_type)", launch)

    def test_the_ui_speaks_the_authored_rows_and_attaches_the_result(self):
        chat = (ROOT / "ui/src/components/Sidebar/DirectorChat.tsx").read_text(encoding="utf-8")
        client = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")

        self.assertIn("const generateVoices = useCallback(async () => {", chat)
        self.assertIn("ttsEpisode({ lines: rows, voices, workspace: activeWorkspace })", chat)
        self.assertIn("await setScriptAudio(episode.path)", chat)
        self.assertIn("/api/v1/audio/tts-episode", client)


class EpisodeDrivesTheLipsTests(unittest.TestCase):
    """With a spoken episode attached, H3 must not speak those lines again.

    This is the mechanism that removes the measured babble: the compiled Ref2VA
    prompt stops carrying the words because the supplied track already says them,
    and the shot is told to follow that audio instead.
    """

    LINE_A = "Hola, bienvenidos al podcast de Maestro."
    LINE_B = "Gracias. Hoy hablamos de voces reales."

    @classmethod
    def _plan(cls, with_episode: bool):
        references = [
            {"type": "image", "role": "Bela (S1)", "image_intent": "identity"},
            {"type": "image", "role": "Roman (S2)", "image_intent": "identity"},
        ]
        if with_episode:
            references.append({
                "type": "audio",
                "role": "the spoken episode",
                "path": "episode.wav",
                "audio_intent": "drive",
            })
        prompt = (
            "SETUP: podcast desk | SUBJECTS: the two hosts from the reference images | "
            "ACTION: they talk to each other | CAMERA: static | ENDING: they nod."
        )
        return {
            "video_prompt": prompt,
            "_director_h3_source_prompt": prompt,
            "_director_h3_model_family": "ref2va",
            "_director_h3_prompt_mode": "ref2va",
            "_director_duration_sec": 6,
            "_director_subjects_on_screen": [
                {"visual_description": "the woman from the reference image (S1)"},
                {"visual_description": "the man from the reference image (S2)"},
            ],
            "_director_dialogue_beats": [
                {"speaker_id": "(S1)", "spoken_text": cls.LINE_A},
                {"speaker_id": "(S2)", "spoken_text": cls.LINE_B},
            ],
            "_director_audio_plan": {"mode": "dialogue_driven", "lip_sync_critical": True},
            "_director_audio_policy": {
                "generated_speech_allowed": True,
                "generated_music_allowed": True,
            },
            "_director_dialogue_manifest_authoritative": True,
            "_director_h3_reference_manifest": references,
        }

    def test_the_episode_supplies_the_words_so_the_prompt_does_not_state_them(self):
        from services.director.h3_dialogue import compile_h3_clip_plans

        plan = self._plan(with_episode=True)
        compile_h3_clip_plans([plan])
        compiled = plan["video_prompt"]

        self.assertNotIn(self.LINE_A, compiled)
        self.assertNotIn(self.LINE_B, compiled)
        self.assertIn("<Audio 1>", compiled)

    def test_without_an_episode_the_model_is_asked_to_speak_them(self):
        from services.director.h3_dialogue import compile_h3_clip_plans

        plan = self._plan(with_episode=False)
        compile_h3_clip_plans([plan])
        compiled = plan["video_prompt"]

        self.assertIn(self.LINE_A, compiled)
        self.assertIn(self.LINE_B, compiled)


if __name__ == "__main__":
    unittest.main()
