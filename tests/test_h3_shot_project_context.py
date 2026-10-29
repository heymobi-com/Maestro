"""One shot reads one stage of the brief, never the whole cast list.

Measured on a real project: every compiled clip carried the entire brief — the SUBJECT
LOCK, the full cast with each character's wardrobe, and all seven stages. Clip 1, a
village street at dusk, carried "S3 (Don Aurelio): man, 60 years old … worn yellowish
alb, faded chasuble", so the priest was rendered in scene after scene even after his
reference image was removed from the manifest. Whatever repeats in every prompt is what
the model draws in every shot.

The brief says the shroud opens onto Don Aurelio once, in Stage 5. That is where he
belongs, and after this only that stage carries his description.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.h3_dialogue import project_subject_lock  # noqa: E402
from services.director_pipeline import (  # noqa: E402
    _h3_shot_project_context,
    _h3_world_context,
)

BRIEF = (
    "SUBJECT LOCK (critical, non-negotiable):\n"
    "- Subject 1 is ALWAYS Abuela Rosa. Subject 2 is ALWAYS la Figura del Purgatorio.\n"
    "- S3-S7 (Don Aurelio, the five children, the villagers) are presence-only.\n"
    "\n"
    "CHARACTERS AND BINDINGS:\n"
    "S1 (Abuela Rosa): woman, 78 years old, black headscarf, grey cardigan.\n"
    "S2 (La Figura del Purgatorio): tall figure wrapped in a black shroud.\n"
    "S3 (Don Aurelio): man, 60 years old, worn yellowish alb, faded chasuble.\n"
    "\n"
    "AMBIENCE:\n"
    "- A stone mountain village at the edge of a dark forest, fog without rain,\n"
    "  cobbled streets, slate roofs, pumpkin lanterns.\n"
    "\n"
    "DESCRIPTION:\n"
    "Stage 1 (fog gathering): dawn-grey kitchen, S1 stands before a wooden table. "
    "Stage 2 (the town waits): a cobbled street, children in paper masks. "
    "Stage 3 (the veil tearing): the black shroud opens, revealing Don Aurelio. "
    "Stage 4 (the closed door): the corridor again, the door now a chapel.\n"
)


class WorldContextTests(unittest.TestCase):
    def test_the_ambience_is_kept(self):
        world = _h3_world_context(BRIEF)

        self.assertIn("stone mountain village", world)
        self.assertIn("slate roofs", world)

    def test_the_cast_list_is_not_part_of_the_world(self):
        world = _h3_world_context(BRIEF)

        self.assertNotIn("Aurelio", world)
        self.assertNotIn("chasuble", world)

    def test_the_world_is_bounded(self):
        long_brief = "AMBIENCE:\n- " + ("fog " * 4000)

        self.assertLessEqual(len(_h3_world_context(long_brief)), 700)


class ShotContextTests(unittest.TestCase):
    def test_a_clip_outside_his_stage_never_learns_the_priest_exists(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertNotIn("Aurelio", context)
        self.assertNotIn("chasuble", context)

    def test_the_priest_survives_in_his_own_stage(self):
        # Stage 3 of 4 sits in the middle of the film; clip 5 of 8 is inside it.
        context = _h3_shot_project_context(BRIEF, 5, 8)

        self.assertIn("Aurelio", context)

    def test_the_clip_gets_its_own_stage_and_nothing_else(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertIn("fog gathering", context)
        self.assertNotIn("the closed door", context)

    def test_the_lock_survives_in_the_shape_the_compiler_parses(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertEqual(project_subject_lock(context), {1: "Abuela", 2: "Figura"})

    def test_the_cast_enumeration_beside_the_lock_is_dropped(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertNotIn("presence-only", context)

    def test_a_brief_with_no_stages_keeps_the_world(self):
        context = _h3_shot_project_context("AMBIENCE:\n- a frozen lake.", 0, 4)

        self.assertIn("frozen lake", context)
        self.assertFalse(context.startswith("\n"))

    def test_a_brief_with_nothing_usable_is_empty(self):
        self.assertEqual(_h3_shot_project_context("A woman waits.", 0, 4), "")


if __name__ == "__main__":
    unittest.main()
