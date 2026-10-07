"""The project switch that lets the plan use continuity between clips.

Shot continuity is a permission the project grants and a decision the plan makes, so the control
has to exist before any of it can be used. Measured before it did: the engine flag was only ever
set by the Studio sequence endpoint and only to False, so the Director path was unreachable from
the interface no matter what the plan declared.

These cases are ours, so they live in their own file.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_APP_DIR = os.path.join(_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.sequence_continuity import OPTION  # noqa: E402

_STORE = os.path.join(_ROOT, "ui", "src", "stores", "useStore.ts")
_CHAT = os.path.join(_ROOT, "ui", "src", "components", "Sidebar", "DirectorChat.tsx")
_TOGGLE = os.path.join(
    _ROOT, "ui", "src", "components", "Sidebar", "ClipContinuityToggle.tsx",
)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TheProjectCanGrantItTests(unittest.TestCase):
    def test_the_switch_asks_for_exactly_what_the_engine_reads(self):
        """The name is the contract: a near miss would leave the option inert in silence."""

        self.assertEqual(OPTION, "_director_sequence_continuity")
        self.assertIn(f"{OPTION}: directorSequenceContinuity,", _read(_STORE))

    def test_it_is_off_until_the_project_says_so(self):
        self.assertIn("directorSequenceContinuity: false", _read(_STORE))

    def test_the_switch_travels_with_the_render_request(self):
        store = _read(_STORE)

        self.assertIn("directorSequenceContinuity", store)
        self.assertIn("setDirectorSequenceContinuity", store)


class TheControlIsWhereTheWorkflowIsTests(unittest.TestCase):
    def test_the_director_setup_panel_shows_it(self):
        self.assertIn("<ClipContinuityToggle locked={locked} />", _read(_CHAT))

    def test_it_is_only_offered_for_a_model_that_can_carry_it(self):
        """The mechanism is the reference path; offering it elsewhere would do nothing."""

        toggle = _read(_TOGGLE)

        self.assertIn("video_strategy === 'omni_reference'", toggle)
        self.assertIn("disabled={locked || !referenceModel}", toggle)

    def test_it_says_what_it_is(self):
        toggle = _read(_TOGGLE)

        self.assertIn("Clip continuity", toggle)
        # The option is the project's permission; the plan is what decides where.
        self.assertIn("the plan says where", toggle)


if __name__ == "__main__":
    unittest.main()
