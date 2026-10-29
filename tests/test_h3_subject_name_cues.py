"""A speaker cue is not a participant's name.

Measured on a real 26-clip drama, clip 1: the short-film planner writes its label
in ``speaker_name`` ("(S1)"), so the compiled prompt introduced the group as
``<Subject 1> ((S1)) is visible in the described blocking`` and defined it as
``<Subject 1> is director_identity_s1 (S1)``. The Subject was introduced by the very
cue the definition is supposed to explain, and the doubled parentheses came from
that cue being written as a name.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.h3_dialogue import (  # noqa: E402
    _h3_name_not_a_cue,
    _label_ref2va_subjects_in_body,
    _ref2va_subject_definitions,
)

PROJECT_CONTEXT = (
    "SUBJECT LOCK (critical, non-negotiable):\n"
    "- Subject 1 is ALWAYS Abuela Rosa. Subject 2 is ALWAYS la Figura del Purgatorio.\n"
)

# The row exactly as the planner wrote it for clip 1 of that run.
CHILDREN_ROW = {
    "visual_description": (
        "Group of children in costumes (witches, devils, angels) holding carved "
        "pumpkin lanterns"
    ),
    "position_or_relation": "midground center, moving towards camera",
    "character_id": "director_identity_s1",
    "speaker_name": "(S1)",
}


class CueIsNotANameTests(unittest.TestCase):
    def test_a_bare_speaker_cue_is_not_a_name(self):
        for value in ("(S1)", "S1", " (s2) ", "S12"):
            self.assertEqual(_h3_name_not_a_cue(value), "")

    def test_a_real_name_survives(self):
        for value in ("Dona Rosa", "Abuela Rosa", "(S1) Rosa"):
            self.assertEqual(_h3_name_not_a_cue(value), value)

    def test_the_body_label_never_carries_doubled_parentheses(self):
        body = _label_ref2va_subjects_in_body(
            "[Shot 1] The street stays empty under the fog.", [CHILDREN_ROW],
        )

        self.assertNotIn("((S1))", body)
        self.assertNotIn("(S1)", body)

    def test_the_definition_does_not_make_the_cue_the_name(self):
        definitions = _ref2va_subject_definitions(
            [CHILDREN_ROW],
            {},
            {},
            body="[Shot 1] Children walk toward the camera.",
            project_context=PROJECT_CONTEXT,
        )

        joined = "\n".join(definitions)
        self.assertNotIn("is (S1)", joined)
        self.assertNotIn("is director_identity_ ", joined)
        # The identity itself must still be named, or the Subject is undefined.
        self.assertIn("director_identity_s1", joined)
        # The performance cue still reaches the renderer, as a cue.
        self.assertIn("(S1)", joined)

    def test_a_named_participant_keeps_its_name(self):
        row = {
            "visual_description": "Dona Rosa at the kitchen table, hands on the wood.",
            "position_or_relation": "centre",
            "character_id": "director_identity_s1",
            "speaker_name": "Dona Rosa",
        }

        definitions = _ref2va_subject_definitions(
            [row], {}, {}, body="[Shot 1] Dona Rosa waits at the table.",
            project_context=PROJECT_CONTEXT,
        )

        self.assertIn("is Dona Rosa", "\n".join(definitions))


if __name__ == "__main__":
    unittest.main()
