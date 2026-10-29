"""A rule about one character is not the film's audio policy.

Reported from use: ``Invalid MiniMax H3 audio policy: generated speech instructions
contradict the authoritative no-generated-speech policy`` -- the run could not be planned
at all.

Measured on ``_director_pipeline_418ae205`` (26 clips, supplied soundtrack, 88 transcript
beats). The brief had grown from 5,003 to 8,016 characters, and the new cast rows said:

    S3 (Don Aurelio): ... No lip-sync, no voice: he appears only inside the opened
    shroud in Stage 5.
    S4 (Los cinco niños del río ...): ... No lip-sync, no voice, no articulated
    speech – only faint distant breathing and soft footsteps.

``director_audio_policy`` read those two lines as a project-wide prohibition, so every
clip was compiled as "generated speech forbidden" while the plan carried spoken beats and
the prompts described a mouth moving with the track. The cast-row shape is the one the
project-brief template asks directors to write, so it is the one shape a global ban can
never hide behind.

Also measured: the policy collapsed the brief's line breaks before the speech check,
which erased the very row boundaries that make the scope readable.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.dialogue_writing import dialogue_forbidden  # noqa: E402
from services.director.h3_dialogue import (  # noqa: E402
    H3DialogueContractError,
    compile_h3_clip_plans,
)
from services.director.source_contract import director_audio_policy  # noqa: E402

# The shape of the brief that broke the run, with its own words.
CAST_SCOPED_BRIEF = """CHARACTERS AND BINDINGS:
S1 (Abuela Rosa): woman, 78, black headscarf, wooden rosary; identity comes from <Picture 1>.
S2 (La Figura del Purgatorio): tall, gaunt, face erased, bronze bells; identity comes from <Picture 2>.
S3 (Don Aurelio): man in his sixties. No lip-sync, no voice: he appears only inside the opened shroud in Stage 5.
S4 (Los cinco niños del río): children in paper masks.
No lip-sync, no voice, no articulated speech - only faint distant breathing and soft footsteps.

AUDIO-DRIVEN GENERATION:
- The image is driven by the supplied audio. Sync lip movements exactly.
- Only S1 (Abuela Rosa) lip-syncs when the voice track assigns her a line.
- S3-S7 keep their mouth closed at all times.
- Do not generate new dialogue, invent speech, or add voices or characters.

NEGATIVE:
- No extra characters, no gore, no new dialogue, no modern clothing, no jump scares.
"""

BANNED_BRIEF = """A silent film. No dialogue at all, music only.

CHARACTERS AND BINDINGS:
S1 (Rosa): an old woman in a black headscarf.
"""


class TheBriefPolicyIsReadAtTheRightScopeTests(unittest.TestCase):
    def test_a_cast_row_that_never_speaks_does_not_ban_speech_for_the_film(self):
        policy = director_audio_policy(CAST_SCOPED_BRIEF)

        self.assertTrue(policy["generated_speech_allowed"])

    def test_a_wrapped_cast_row_is_still_about_that_character(self):
        # The S4 row above wraps onto a second line; the wrap is the same contract.
        brief = (
            "CHARACTERS AND BINDINGS:\n"
            "S4 (the five children): kids in white paper masks.\n"
            "No lip-sync, no voice, no articulated speech.\n\n"
            "AUDIO-DRIVEN GENERATION:\n- The supplied audio owns every voice.\n"
        )

        self.assertFalse(dialogue_forbidden(brief))

    def test_a_global_ban_after_a_cast_row_is_still_global(self):
        brief = (
            "S3 (Don Aurelio): tall.\n"
            "No voice.\n\n"
            "NEGATIVE:\n"
            "- No dialogue in this film at all, music only.\n"
        )

        self.assertTrue(dialogue_forbidden(brief))

    def test_a_silent_film_brief_still_forbids_generated_speech(self):
        self.assertFalse(director_audio_policy(BANNED_BRIEF)["generated_speech_allowed"])

    def test_no_new_dialogue_is_not_a_ban_on_dialogue(self):
        """The track already speaks; the line forbids inventing extra words."""

        self.assertFalse(dialogue_forbidden(
            "Do not generate new dialogue, invent speech, or add voices."
        ))

    def test_a_subject_row_that_never_speaks_is_scoped_too(self):
        brief = "<Subject 2> (S2): tall. No speech; only bells.\n<Subject 1> (S1) narrates."

        self.assertFalse(dialogue_forbidden(brief))

    def test_naming_a_speech_act_outside_any_cast_row_is_still_a_ban(self):
        self.assertTrue(dialogue_forbidden("Nobody speaks in this scene."))


def _plan(prompt: str, *, policy: dict, audio_plan: dict | None = None) -> dict:
    return {
        "video_prompt": prompt,
        "_director_h3_source_prompt": prompt,
        "_director_h3_model_family": "ref2va",
        "_director_h3_prompt_mode": "ref2va",
        "_director_duration_sec": 6,
        "_director_project_context": "",
        "_director_subjects_on_screen": [],
        "_director_dialogue_beats": [],
        "_director_audio_plan": audio_plan or {},
        "_director_audio_policy": policy,
        "_director_dialogue_manifest_authoritative": True,
        "_director_h3_reference_manifest": [
            {"type": "image", "role": "Rosa (S1)", "image_intent": "identity"},
        ],
    }


BAN = {"generated_speech_allowed": False, "generated_music_allowed": True}
SPEAKING_SHOT = "A medium shot in the church. Abuela Rosa speaks softly to the villagers."


class TheSuppliedTrackOwnsTheMouthTests(unittest.TestCase):
    """A clip whose own audio plan came through neutral was compiled as silent.

    The manifest cannot show a soundtrack that the pipeline passes as an audio guide, so
    the shot read as "no speech allowed and no audio at all", and describing the mouth
    that has to sync was refused as a request to generate speech.
    """

    def test_a_supplied_track_legalises_the_synced_mouth(self):
        plan = _plan(SPEAKING_SHOT, policy=BAN)

        compile_h3_clip_plans([plan], supplied_track=True)

        self.assertIn("speaks softly", plan["video_prompt"])

    def test_without_a_track_the_same_shot_is_still_refused(self):
        plan = _plan(SPEAKING_SHOT, policy=BAN)

        with self.assertRaises(H3DialogueContractError):
            compile_h3_clip_plans([plan], supplied_track=False)

    def test_the_refusal_names_the_sentence_to_change(self):
        plan = _plan(SPEAKING_SHOT, policy=BAN)

        with self.assertRaises(H3DialogueContractError) as caught:
            compile_h3_clip_plans([plan], supplied_track=False)

        message = str(caught.exception)
        self.assertIn("Abuela Rosa speaks softly", message)
        self.assertIn("supplied track", message)


def _blocks(prompt: str) -> int:
    from services.director.h3_dialogue import _dialogue_spans

    return len(_dialogue_spans(prompt)[0])


class ASuppliedTrackStillStatesTheLinesItOwnsTests(unittest.TestCase):
    """The measured regression: "Shot 7: expected 4 dialogue block(s), found 0".

    Shot 7 of ``_director_pipeline_1507e7df`` is a mouthless speaker: the planner
    marked it ``audio_driven`` with ``lip_sync_critical: False`` and wrote four lines
    into it, so ``reconcile_audio_plan_with_dialogue`` turned it into
    ``dialogue_driven`` + ``lip_sync_critical: True``.

    ``states_the_lines`` answers True for that shot without a driving track and False
    with one. Widening the flag that the *policy* scan uses also widened the flag that
    decides which beats the compiler writes, so the shot was compiled with zero blocks
    while the caller still required four.
    """

    BEATS = [
        {"speaker_id": "(S2)", "spoken_text": "La máscara se ha movido."},
        {"speaker_id": "(S2)", "spoken_text": "Ahora está frente a mí."},
        {"speaker_id": "(S2)", "spoken_text": "Algo me toca la nuca."},
        {"speaker_id": "(S2)", "spoken_text": "Un sonido pequeño, frío."},
    ]

    def _mouthless_plan(self) -> dict:
        plan = _plan(
            "[Shot 1] The empty paper mask hangs at the window. The figure sways.",
            policy={"generated_speech_allowed": True, "generated_music_allowed": True},
            audio_plan={
                "mode": "dialogue_driven",
                "timing_anchor": "audio",
                "lip_sync_critical": True,
            },
        )
        plan["_director_dialogue_beats"] = list(self.BEATS)
        return plan

    def test_a_supplied_track_keeps_the_beats_the_caller_requires(self):
        plan = self._mouthless_plan()

        compile_h3_clip_plans([plan], supplied_track=True)

        self.assertEqual(_blocks(plan["video_prompt"]), len(self.BEATS))

    def test_the_same_shot_writes_the_same_beats_without_the_track(self):
        plan = self._mouthless_plan()

        compile_h3_clip_plans([plan], supplied_track=False)

        self.assertEqual(_blocks(plan["video_prompt"]), len(self.BEATS))

    def test_the_track_does_not_widen_which_beats_are_stated(self):
        """The two flags are separate: policy scope must not choose the beats."""

        source = (
            (os.path.join(_APP_DIR, "services", "director", "h3_dialogue.py"))
        )
        with open(source, "r", encoding="utf-8") as handle:
            text = handle.read()

        self.assertIn("policy_driving_audio = has_driving_audio or driving_audio", text)
        self.assertNotIn("if driving_audio:\n        has_driving_audio = True", text)


if __name__ == "__main__":
    unittest.main()
