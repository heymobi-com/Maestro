"""The template brief must stay a shape the pipeline can actually read.

docs/Maestro-brief-valdecuna.md is the corrected brief, and every rule in it was earned
by a failure: a lock the parser ignored, a shot with no position in the story, one
camera move applied to the whole film, a character appearing in scenes that were not
his. This test runs the document's own text block through the real parsers, so editing
the document into a shape the code no longer understands fails here instead of after a
render.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.h3_dialogue import project_subject_lock  # noqa: E402
from services.director.planners.short_film import (  # noqa: E402
    _brief_stage_list,
    _stage_for_clip,
)
from services.director_pipeline import _h3_shot_project_context  # noqa: E402

DOC = ROOT / "docs/Maestro-brief-valdecuna.md"


def _brief_from_doc() -> str:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"```text\n(.*?)```", text, re.S)
    if not match:
        raise AssertionError("the document has no ```text block to paste")
    return match.group(1)


BRIEF = _brief_from_doc()
TOTAL_CLIPS = 26


class TemplateBriefTests(unittest.TestCase):
    def test_the_lock_is_in_the_shape_the_compiler_parses(self):
        self.assertEqual(project_subject_lock(BRIEF), {1: "Abuela", 2: "Figura"})

    def test_the_stages_are_numbered_and_complete(self):
        stages = _brief_stage_list(BRIEF)

        self.assertEqual([stage["number"] for stage in stages], list(range(1, 8)))
        self.assertEqual(stages[0]["label"], "kitchen, dawn")
        self.assertEqual(stages[6]["label"], "corridor, dawn")

    def test_each_stage_names_one_place(self):
        stages = _brief_stage_list(BRIEF)

        for stage in stages:
            self.assertLessEqual(len(stage["text"]), 400)

    def test_the_progression_never_goes_backwards(self):
        stages = _brief_stage_list(BRIEF)
        seen = [_stage_for_clip(stages, index, TOTAL_CLIPS)["number"] for index in range(TOTAL_CLIPS)]

        self.assertEqual(seen, sorted(seen))

    def test_the_first_clip_opens_the_film_and_the_last_closes_it(self):
        stages = _brief_stage_list(BRIEF)

        self.assertEqual(_stage_for_clip(stages, 0, TOTAL_CLIPS)["number"], 1)
        self.assertEqual(_stage_for_clip(stages, TOTAL_CLIPS - 1, TOTAL_CLIPS)["number"], 7)

    def test_the_priest_exists_only_in_his_own_stage(self):
        stages = _brief_stage_list(BRIEF)
        context_by_stage: dict[int, str] = {}
        for index in range(TOTAL_CLIPS):
            number = _stage_for_clip(stages, index, TOTAL_CLIPS)["number"]
            context_by_stage[number] = _h3_shot_project_context(BRIEF, index, TOTAL_CLIPS)

        mentions = sorted(
            number for number, context in context_by_stage.items()
            if re.search(r"aurelio|chasuble|alb", context, re.I)
        )
        self.assertEqual(mentions, [5])

    def test_a_clip_never_learns_the_whole_cast(self):
        context = _h3_shot_project_context(BRIEF, 0, TOTAL_CLIPS)

        for name in ("Aurelio", "campana", "puerta negra", "Niños"):
            self.assertNotIn(name.lower(), context.lower())

    def test_the_camera_has_no_rule_for_the_whole_film(self):
        # "whenever", "always", "every time" and "throughout" turn a choice into a
        # rule the planner applies to every shot it writes.
        camera = BRIEF.split("CAMERA", 1)[1].split("AMBIENCE", 1)[0]

        for word in ("whenever", "always", "every time", "throughout"):
            self.assertNotIn(word, camera.lower())

    def test_the_transformation_is_explicitly_denied(self):
        self.assertIn("no transformation", BRIEF)
        self.assertIn("two different beings", BRIEF)


if __name__ == "__main__":
    unittest.main()
