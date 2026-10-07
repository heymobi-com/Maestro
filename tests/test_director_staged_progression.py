"""A staged process is told across shots, not repeated inside every shot.

Reported from use: a dramatic instruction (plants growing, blooming and drying; rain
falling) "lo hace en un solo clip y lo repite mas delante como si ese clip fuera una sola
transicion creando efectos super rapidos que pierden su intencion". The director asked for
the obvious thing: a story is not told in seven seconds repeated over and over, it
transitions from one shot to the next, sometimes slowly enough for the detail to read.

Measured on the affected project's own plan (30 clips, 2.6s to 9.6s each): the complete
plant process -- sprouting, blooming, branches, leaves, drying -- appeared with all six
terms inside EACH of clips 8 to 15, and again at 23 and 29. Rain or lightning appeared in
26 of the 30 shots.

The planner is told each video_prompt must be self-contained, which is right for setting,
wardrobe and identity, and no guide says anything about advancing a process across shots. So
the contract that was missing is stated once, in a guide of ours, and attached where every
planner's system prompt passes: BasePlanner._call_llm_json. That is the only place that
covers all five workflows -- video musical, short film (audio and story), podcast and viral
video -- because podcast and viral video load no guide at all.
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director import staged_progression  # noqa: E402
from services.director.planners.base import BasePlanner  # noqa: E402
from services.director.staged_progression import with_shot_progression  # noqa: E402

ADDENDUM_MARKER = "STAGED PROGRESSION"


def flat(text):
    """The addendum is wrapped prose; compare it with the wrapping removed."""

    return " ".join(str(text or "").split())


class _RecordingPlanner(BasePlanner):
    """The smallest planner that can be built: it records what it was asked to send."""

    skill_type = "music_video"

    def __init__(self):
        super().__init__(llm_generate=self._answer)
        self.system_prompts = []

    def plan(self, **kwargs):
        """The base declares this abstract; this test never plans anything."""

        return None

    def _answer(self, **kwargs):
        self.system_prompts.append(str(kwargs.get("system_prompt") or ""))
        return '[{"shot": 1}]'


class TheStagedProgressionContractTests(unittest.TestCase):
    def _what_the_planner_sends(self):
        planner = _RecordingPlanner()
        planner._call_llm_json(
            user_prompt="plan the shots of this film", system_prompt="BASE RULES",
        )
        self.assertTrue(planner.system_prompts)
        return planner.system_prompts[0]

    def test_every_planner_sends_the_contract(self):
        """The one place all five workflows pass through, not one guide per mode."""

        sent = self._what_the_planner_sends()

        self.assertIn("BASE RULES", sent)
        self.assertIn(ADDENDUM_MARKER, sent)

    def test_the_planner_hook_is_what_the_planners_call(self):
        source = inspect.getsource(BasePlanner._call_llm_json)

        self.assertIn(
            "with_shot_progression(system_prompt, self.skill_type)", source,
        )

    def test_the_contract_states_one_stage_per_shot(self):
        sent = flat(self._what_the_planner_sends())

        self.assertIn("Never write the whole process into a single shot", sent)
        self.assertIn("Give each shot ONE stage", sent)

    def test_the_contract_fits_the_stage_to_the_shot_duration(self):
        """A three-second shot carrying three stages is the reported fast flashing."""

        self.assertIn(
            "a three-second shot holds one small visible change",
            flat(self._what_the_planner_sends()),
        )

    def test_the_contract_requires_naming_the_stage_a_shot_inherits(self):
        """The one place a continuity reference is required rather than banned."""

        self.assertIn(
            "continuity reference is required rather than forbidden",
            flat(self._what_the_planner_sends()),
        )

    def test_a_prompt_that_already_carries_it_is_not_given_it_twice(self):
        """The JSON-fix retry reuses the same system prompt a second time."""

        once = with_shot_progression("BASE RULES", "music_video")
        twice = with_shot_progression(once, "music_video")

        self.assertEqual(twice, once)
        self.assertEqual(twice.count(ADDENDUM_MARKER), 1)

    def test_every_film_planner_is_covered(self):
        """The four skills the schema names, and the two short-film paths map to one."""

        for skill in ("music_video", "short_film", "podcast", "viral_video"):
            with self.subTest(skill=skill):
                self.assertIn(ADDENDUM_MARKER, with_shot_progression("BASE", skill))

    def test_a_caller_that_is_not_planning_shots_is_left_alone(self):
        """BasePlanner is also used directly, and that prompt is not a shot list."""

        self.assertEqual(with_shot_progression("Bewahre Grüße", "integrity_test"), "Bewahre Grüße")
        self.assertEqual(with_shot_progression("BASE", ""), "BASE")

    def test_a_missing_addendum_never_breaks_a_plan(self):
        with mock.patch.object(staged_progression, "_ADDENDUM", "no-such-guide.md"):
            self.assertEqual(
                with_shot_progression("BASE RULES", "music_video"), "BASE RULES",
            )

    def test_an_empty_system_prompt_still_gets_the_contract(self):
        self.assertIn(ADDENDUM_MARKER, with_shot_progression("", "podcast"))


if __name__ == "__main__":
    unittest.main()

