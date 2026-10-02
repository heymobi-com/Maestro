"""The fork's audio-driven dialogue contract, pinned where it can be seen.

Upstream v2.4.0 decided that a plan driven by supplied audio keeps its transcript as timing
metadata and drops the lines from the prompt: "supplied driving audio owns every audible
voice". That assumes the model can perform a line it is never told. It cannot, and the
reported 190-shot project rendered with zero ``<d>`` lines, the wrong speakers, the wrong
words, and mouths out of step.

The decision and its reasoning live in `services/director/audio_driven_transcript.py`, because
it is spread over three gates in the compiler. This file pins the behaviour end to end, the
policy itself, and the case the policy must NOT change: a music video whose uploaded song owns
the vocals reports ``audio_driven`` too, and compiling its lyrics as generated speech tripped
upstream's own tests (tests/test_director_music_audio_contract.py), which is why the plan's
own ``lip_sync_critical`` and not the mode decides.
"""

import os
import sys
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.audio_driven_transcript import states_the_lines  # noqa: E402
from services.director.h3_dialogue import compile_h3_clip_plans  # noqa: E402


def _audio_driven_plan(lip_critical=True):
    """The reported shape: a transcript beat, an audio reference that drives it."""
    plan = {
        "video_prompt": (
            "Mara lifts her hand in greeting, then smiles. "
            "<d>[Spanish] Hola, ¿cómo estás?</d> "
            "overall_soundscape: Light wind. non_diegetic_music: N/A."
        ),
        "_director_h3_model_family": "ref2va",
        "_director_subjects_on_screen": [{
            "character_id": "mara",
            "speaker_name": "Mara",
            "visual_description": "Mara wearing a green coat",
            "wardrobe": "green coat",
            "position_or_relation": "screen-left",
        }],
        "_director_dialogue_beats": [{
            "speaker_id": "mara",
            "spoken_text": "Hola, ¿cómo estás?",
            "language": "Spanish",
            "delivery": "warmly",
            "physical_cue": "she waves once",
        }],
        "_director_audio_plan": {
            "mode": "audio_driven", "ambience": "Light wind",
            # The plan's own declaration that the mouths have to follow this line, which is
            # what a film whose dialogue track supplies the words carries (190/190 clips).
            "lip_sync_critical": True,
        },
    }
    if not lip_critical:
        # The music video's shape: the same mode, and no mouthed line.
        del plan["_director_audio_plan"]["lip_sync_critical"]
    references = [{
        "type": "audio",
        "role": "the supplied dialogue performance",
        "audio_intent": "drive",
    }]
    return plan, references


class TheLinesArePerformed(unittest.TestCase):
    def test_an_audio_driven_shot_states_who_speaks_and_what(self):
        plan, references = _audio_driven_plan()

        compile_h3_clip_plans(
            [plan], prompt_modes=["ref2va"], durations=[6], reference_manifests=[references],
        )

        self.assertIn("lifts her hand in greeting", plan["video_prompt"])
        self.assertIn("<d>[Spanish] Hola, ¿cómo estás?</d>", plan["video_prompt"])
        self.assertEqual(plan["_director_dialogue_beats"][0]["language"], "Spanish")

    def test_a_music_videos_audio_driven_shot_keeps_them_as_metadata(self):
        """The same mode, the opposite outcome, because the plan says the mouths do not move.

        This is upstream's contract for a music video whose uploaded song owns the vocals
        (tests/test_director_music_audio_contract.py), reached through this same gate.
        """
        plan, references = _audio_driven_plan(lip_critical=False)

        compile_h3_clip_plans(
            [plan], prompt_modes=["ref2va"], durations=[6], reference_manifests=[references],
        )

        self.assertNotIn("<d>", plan["video_prompt"])
        self.assertIn("mapped driving audio", plan["video_prompt"])
        self.assertEqual(
            plan["_director_dialogue_beats"][0]["spoken_text"], "Hola, ¿cómo estás?"
        )

    def test_the_audio_still_drives_the_shot(self):
        """Keeping the lines must not cost the binding: the voice is the supplied track.

        The sentence lives in `non_diegetic_music`, so the two are not in conflict: one says
        where the soundtrack comes from, the tagged lines say what is performed over it.
        """
        plan, references = _audio_driven_plan()

        compile_h3_clip_plans(
            [plan], prompt_modes=["ref2va"], durations=[6], reference_manifests=[references],
        )

        self.assertIn("mapped driving audio", plan["video_prompt"])

    def test_a_shot_with_nothing_to_say_keeps_upstreams_contract(self):
        """No beats, no lines: the prompt still hands the voice to the mapped audio."""
        plan, references = _audio_driven_plan()
        plan["_director_dialogue_beats"] = []
        plan["video_prompt"] = "Mara watches the horizon. overall_soundscape: Light wind."

        compile_h3_clip_plans(
            [plan], prompt_modes=["ref2va"], durations=[6], reference_manifests=[references],
        )

        self.assertIn("mapped driving audio", plan["video_prompt"])
        self.assertNotIn("<d>", plan["video_prompt"])


class ThePolicyItself(unittest.TestCase):
    """One place decides, so a merge cannot restore upstream through a single gate."""

    def test_an_audio_driven_plan_states_its_lines(self):
        self.assertTrue(states_the_lines(audio_mode="audio_driven", audio_plan={"lip_sync_critical": True}))
        self.assertTrue(states_the_lines(
            audio_mode="audio_driven", audio_plan={"lip_sync_critical": True}, driving_audio=True,
        ))
        self.assertTrue(states_the_lines(
            audio_mode="audio_driven", audio_plan={"lip_sync_critical": True},
            driving_audio_reference=True,
        ))

    def test_the_music_videos_audio_driven_plan_does_not(self):
        """The mode alone is not the answer: this is the plan upstream pinning the opposite.

        A music video with an uploaded song reports ``audio_driven`` and declares no
        lip-critical line, because the track's vocal is not mouthed. Treating the mode as the
        whole answer compiled its lyrics as generated speech, which the upstream music-audio
        contract tests caught.
        """
        self.assertFalse(states_the_lines(audio_mode="audio_driven"))
        self.assertFalse(states_the_lines(
            audio_mode="audio_driven", audio_plan={"mode": "audio_driven"}, driving_audio=True,
        ))
        self.assertFalse(states_the_lines(
            audio_mode="audio_driven", audio_plan={"lip_sync_critical": False},
            driving_audio_reference=True,
        ))
        # A plan saved with the string must not read as mouthed.
        self.assertFalse(states_the_lines(
            audio_mode="audio_driven", audio_plan={"lip_sync_critical": "False"},
        ))

    def test_a_music_driven_plan_does_not(self):
        """Those lyrics belong to the soundtrack, not to a performer in frame."""
        self.assertFalse(states_the_lines(audio_mode="music_driven"))
        self.assertFalse(states_the_lines(audio_mode="music_driven", driving_audio=True))

    def test_other_modes_keep_upstreams_behaviour(self):
        self.assertFalse(states_the_lines(audio_mode="dialogue_driven", driving_audio=True))
        self.assertFalse(
            states_the_lines(audio_mode="dialogue_driven", driving_audio_reference=True)
        )
        self.assertFalse(states_the_lines(audio_mode="ambient_only", driving_audio=True))

    def test_a_plain_dialogue_plan_states_them(self):
        self.assertTrue(states_the_lines(audio_mode="dialogue_driven"))
        self.assertTrue(states_the_lines(audio_mode=""))

    def test_the_mode_is_read_forgivingly(self):
        """It arrives from a planner's JSON: case and spacing are not a contract."""
        self.assertTrue(states_the_lines(
            audio_mode="  Audio_Driven  ", audio_plan={"lip_sync_critical": True},
        ))
        self.assertFalse(states_the_lines(audio_mode="  MUSIC_DRIVEN "))


if __name__ == "__main__":
    unittest.main()
