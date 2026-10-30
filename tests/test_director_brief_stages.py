"""Which stage of the brief a clip belongs to, decided by position, not by the model.

A real 26-clip drama was written as seven stages and still put stage 7 (the closing
chapel) in clip 11 and the bell in the forest in clip 6, whose lines were about street
lamps. The planner was free to pick a stage per shot, so it picked badly and out of
order. Reading the stages and handing each clip the one its position has reached makes
the progression the code's decision.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.planners.short_film import (  # noqa: E402
    _brief_stage_list,
    _stage_for_clip,
)

BRIEF = (
    "CHARACTERS AND BINDINGS:\n"
    "S3 (Don Aurelio): he appears only inside the opened shroud in Stage 5.\n"
    "S4 (Los cinco niños): they arrive with the light in Stage 6.\n"
    "\n"
    "DESCRIPTION:\n"
    "One continuous night. Stage 1 (fog gathering): dawn-grey kitchen, S1 at the table. "
    "Stage 2 (the town waits): a cobbled street, children in paper masks. "
    "Stage 3 (the bell from the forest): a broken bell sways between twisted trees. "
    "Stage 4 (the passage): a corridor, the black door ajar. "
    "Stage 5 (the veil tearing): the shroud opens onto Don Aurelio. "
    "Stage 6 (light breaking): five candles in the church. "
    "Stage 7 (the closed door): the corridor again, the door now a chapel.\n"
)


class StageParsingTests(unittest.TestCase):
    def test_every_stage_is_read_in_order(self):
        stages = _brief_stage_list(BRIEF)

        self.assertEqual([s["number"] for s in stages], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(stages[0]["label"], "fog gathering")
        self.assertIn("dawn-grey kitchen", stages[0]["text"])

    def test_a_stage_mentioned_in_the_cast_rules_is_not_a_stage(self):
        # "he appears only inside the opened shroud in Stage 5" comes first in the
        # brief; taking it as the sequence start would put the whole film in stage 5.
        stages = _brief_stage_list(BRIEF)

        self.assertEqual(len(stages), 7)
        self.assertIn("broken bell", stages[2]["text"])

    def test_a_brief_without_stages_yields_nothing(self):
        self.assertEqual(_brief_stage_list("A woman waits in a kitchen."), [])
        self.assertEqual(_brief_stage_list(""), [])

    def test_stages_are_bounded(self):
        stages = _brief_stage_list(BRIEF)

        for stage in stages:
            self.assertLessEqual(len(stage["text"]), 400)


class StageAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.stages = _brief_stage_list(BRIEF)

    def test_the_first_clip_is_the_first_stage(self):
        self.assertEqual(_stage_for_clip(self.stages, 0, 26)["number"], 1)

    def test_the_last_clip_is_the_last_stage(self):
        self.assertEqual(_stage_for_clip(self.stages, 25, 26)["number"], 7)

    def test_the_progression_never_goes_backwards(self):
        seen = [_stage_for_clip(self.stages, index, 26)["number"] for index in range(26)]

        self.assertEqual(seen, sorted(seen))

    def test_a_batch_offset_lands_in_the_right_stage(self):
        # Clips 9-12 of 26 are stage 3, and a batch of 12 must not restart at stage 1.
        stages_in_batch = {
            _stage_for_clip(self.stages, 8 + index, 26)["number"] for index in range(4)
        }

        self.assertEqual(stages_in_batch, {3})

    def test_no_stages_and_no_clips_are_safe(self):
        self.assertIsNone(_stage_for_clip([], 0, 26))
        self.assertIsNone(_stage_for_clip(self.stages, 0, 0))


class ParagraphBriefTests(unittest.TestCase):
    """A brief rewritten without stage labels still has ordered beats."""

    BRIEF = (
        "AMBIENCE:\n"
        "Stone mountain village at the edge of a dark forest, fog without rain.\n"
        "\n"
        "DESCRIPTION:\n"
        "One night in Valdecuna. Dawn-grey kitchen: Rosa before a wooden table. "
        "Cobbled street: children in paper masks, streetlamps flicker in unison. "
        "Broken bronze bell swaying between twisted trees, blue moonlight. "
        "Narrow stone corridor: the black door ajar; the Figure enters behind Rosa. "
        "The Figure's shroud opens, revealing Don Aurelio. Small village church: "
        "candles lit, Rosa kneels. Rosa's corridor again: the door shut, the clock ticking.\n"
        "\n"
        "NEGATIVE:\n"
        "No extra characters.\n"
    )

    def test_the_description_becomes_ordered_beats(self):
        beats = _brief_stage_list(self.BRIEF)

        self.assertGreaterEqual(len(beats), 5)
        self.assertEqual([b["number"] for b in beats], list(range(1, len(beats) + 1)))

    def test_the_setting_sentence_joins_the_beat_that_follows_it(self):
        beats = _brief_stage_list(self.BRIEF)

        self.assertIn("Dawn-grey kitchen", beats[0]["text"])
        self.assertNotEqual(beats[0]["text"], "One night in Valdecuna")

    def test_the_negative_section_is_not_a_beat(self):
        beats = _brief_stage_list(self.BRIEF)

        self.assertFalse([b for b in beats if "No extra characters" in b["text"]])

    def test_the_priest_appears_in_one_beat_only(self):
        beats = _brief_stage_list(self.BRIEF)

        self.assertEqual(len([b for b in beats if "Aurelio" in b["text"]]), 1)

    def test_progression_stays_forward(self):
        beats = _brief_stage_list(self.BRIEF)
        seen = [_stage_for_clip(beats, index, 26)["number"] for index in range(26)]

        self.assertEqual(seen, sorted(seen))


if __name__ == "__main__":
    unittest.main()
