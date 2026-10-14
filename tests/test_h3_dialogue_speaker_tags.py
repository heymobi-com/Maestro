"""A line names its speaker beside itself, so the renderer can attribute it.

Reported as: "MiniMax H3 Omni would refuse these spoken lines: no cue names the character who speaks
them... The line ... is introduced by 's are deep, chest-based, and controlled. Facial expressions
show concentration and strain:'". The planner had closed its description with a voice description
instead of naming anyone -- which is what a writer free of a grammar does.

The blocks are inserted into ``detailed_description``, and a cue is read clipped to the field it sits
in, so the attribution carried in the DIALOGUE AND VOCAL PERFORMANCE field never reaches the
renderer's check. The tag has to be beside the line, and only where the line lacks one.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from services.director import h3_speaker_cues  # noqa: E402
from services.director.h3_dialogue import (  # noqa: E402
    _insert_dialogue_blocks,
    _speaker_map,
    h3_unresolved_speaker_cue_problems,
)

SUBJECTS = [
    {"character_id": "valeria", "speaker_name": "Valeria"},
    {"character_id": "marco", "speaker_name": "Marco"},
]
BEATS = [
    {"speaker_id": "valeria", "spoken_text": "Ready to start?"},
    {"speaker_id": "marco", "spoken_text": "Cut the chatter."},
]
# The tail the planner wrote, in the shape that was reported: it names nobody.
OFFENDING = (
    "detailed_description: The camera holds on the two of them. Her voice is deep, chest-based, and "
    "controlled. Facial expressions show concentration and strain:\n"
    "overall_soundscape: a quiet room"
)


def _compiled(prompt, beats=BEATS, subjects=SUBJECTS) -> str:
    cleaned, blocks = h3_speaker_cues.compile_blocks(prompt, beats, subjects, "English")
    return _insert_dialogue_blocks(cleaned, blocks)


class SpeakerTagTests(unittest.TestCase):
    def test_a_line_with_no_named_cue_gets_its_tag(self):
        prompt = _compiled(OFFENDING)
        self.assertIn("<Subject 1> says: <d", prompt)
        self.assertIn("<Subject 2> says: <d", prompt)

    def test_the_reported_failure_is_now_renderable(self):
        # The whole point: the shot the renderer refused is accepted.
        self.assertEqual(h3_unresolved_speaker_cue_problems(_compiled(OFFENDING)), [])

    def test_without_the_tags_the_reported_failure_still_shows(self):
        # And this is what the operator saw: the same prompt with bare canonical blocks. The check
        # does not fire for a shot with one voice, which is why these are all multi-voice shots.
        prompt = _compiled(OFFENDING, subjects=[])
        problems = h3_unresolved_speaker_cue_problems(prompt)
        self.assertEqual(len(problems), 2)
        self.assertIn("names no", problems[0])

    def test_a_cue_that_already_carries_a_tag_is_left_alone(self):
        already = (
            "detailed_description: The camera holds. <Subject 1> says: <Subject 2> says:\n"
            "overall_soundscape: a quiet room"
        )
        prompt = _compiled(already)
        self.assertEqual(prompt.count("<Subject 1> says:"), 1)
        self.assertEqual(prompt.count("<Subject 2> says:"), 1)

    def test_compiling_the_result_again_returns_it_unchanged(self):
        # The compiler's own contract, and the reason compile_blocks also returns the text: the tags
        # it adds sit outside the <d> span, so a second pass has to take its own tags out again.
        from services.director.h3_dialogue import compile_h3_vocal_contract

        first, _ = compile_h3_vocal_contract(OFFENDING, SUBJECTS, BEATS)
        again, _ = compile_h3_vocal_contract(first, SUBJECTS, BEATS)
        self.assertEqual(again, first)

    def test_the_lines_keep_their_order(self):
        prompt = _compiled(OFFENDING)
        self.assertLess(prompt.index("<Subject 1>"), prompt.index("<Subject 2>"))
        self.assertLess(prompt.index("Ready to start?"), prompt.index("Cut the chatter"))

    def test_a_speaker_that_cannot_be_resolved_still_gets_a_tag(self):
        # Same fallback the spoken-lines instruction list already makes, rather than an
        # unrenderable shot: (S1) when the planner named nobody.
        speakers = _speaker_map(SUBJECTS)
        self.assertEqual(h3_speaker_cues.subject_tag(speakers, {"speaker_id": ""}), "<Subject 1>")
        self.assertEqual(h3_speaker_cues.subject_tag(speakers, {"speaker_id": "marco"}), "<Subject 2>")

    def test_a_beat_with_no_words_adds_no_block(self):
        self.assertEqual(_compiled(OFFENDING, beats=[{"speaker_id": "valeria", "spoken_text": " "}]).count("<d"), 0)


LONG_DELIVERY = (
    "rises. Vocalizations are low-register, guttural, and rhythmic. Movements are smooth, not jerky, "
    "with deliberate pacing and sustained eye contact throughout the whole action while the body "
    "answers every touch without any hesitation at all in the moment"
)


class SpokenSentenceTests(unittest.TestCase):
    """The sentence the compiler builds around a line, and the cue window it has to fit."""

    BEAT = {
        "speaker_name": "Valeria",
        "stable_id": "(S1)",
        "delivery": LONG_DELIVERY,
        "tag": "<d>[English] Hold still.</d>",
    }

    def _prompt(self, sentence):
        return f"detailed_description: They move together. {sentence} overall_soundscape: a room"

    def test_a_long_delivery_keeps_the_speaker_beside_the_line(self):
        # Measured: 251 characters of delivery put "Valeria (S1) speaks" outside the 160 the check
        # reads back, and the shot was refused for naming nobody.
        sentence = h3_speaker_cues.spoken_sentence(dict(self.BEAT))
        self.assertNotIn(LONG_DELIVERY, sentence, "the delivery was bounded to fit the window")
        self.assertEqual(h3_unresolved_speaker_cue_problems(self._prompt(sentence)), [])

    def test_a_delivery_that_fits_is_left_verbatim(self):
        # Nothing changes for the ordinary case: the sentence is what it always was.
        sentence = h3_speaker_cues.spoken_sentence(dict(self.BEAT, delivery="low and deliberate"))
        self.assertEqual(
            sentence,
            "Valeria (S1) speaks low and deliberate: <d>[English] Hold still.</d>.",
        )

    def test_a_beat_with_no_delivery_keeps_its_shape(self):
        sentence = h3_speaker_cues.spoken_sentence(dict(self.BEAT, delivery=""))
        self.assertEqual(sentence, "Valeria (S1) speaks: <d>[English] Hold still.</d>.")

    def test_a_longer_speaker_name_shortens_the_delivery(self):
        # The room is measured against the real prefix, so a long name cannot push the line away.
        largo = h3_speaker_cues.spoken_sentence(dict(self.BEAT, speaker_name="Valeria Mariana"))
        corto = h3_speaker_cues.spoken_sentence(dict(self.BEAT))
        self.assertLess(len(largo), len(corto) + len(" Mariana"))
        self.assertEqual(h3_unresolved_speaker_cue_problems(self._prompt(largo)), [])


if __name__ == "__main__":
    unittest.main()
