"""What the planner is told to design, and which local model gets to reason about it.

Two measured gaps, both from the same audit of a real project:

* ``camera_plan`` allows five decisions, but the planner's output example showed three and only
  ``framing`` was required, so ``angle`` and ``lens_feel`` came back empty in **all 30 shots of
  the project, in two different local models**: the two choices that most change how a frame
  reads were never made.
* The registry has a small dense Gemma and a 26B MoE whose thinking is activated by injecting
  ``<|think|>``. ``BasePlanner._call_llm_json`` only recognised the ``gemma`` style, so the MoE
  fell through to the unknown-style default: thinking off. Measured on the user's own runs, the
  MoE's thinking text was 0 characters in all three of its passes while the dense model produced
  6k, 3k and 4k characters. Thinking off is the right answer there -- planning relies on the JSON
  grammar, which the local path only reaches with thinking off -- but it has to be a decision the
  code states, not a fall-through, so the reason lives in ``base.py`` and is pinned here.

These cases are ours, so they live in their own file.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.planners.base import BasePlanner  # noqa: E402

_GUIDES = os.path.join(_APP_DIR, "services", "llm_guides", "director")
_VALID = '[{"scene_goal": "x"}]'
_SCHEMA = {"type": "array", "items": {"type": "object"}, "maxItems": 3}


class _Planner(BasePlanner):
    skill_type = "test"

    def plan(self, **kwargs):  # pragma: no cover - abstract filler
        raise NotImplementedError


def _planner_with(responses):
    calls = []

    def fake_gen(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    return _Planner(llm_generate=fake_gen, llm_generate_streaming=fake_gen), calls


class TheReasoningBudgetIsAModelDecisionTests(unittest.TestCase):
    """A style the switch does not name is the accident these cases exist to prevent."""

    def _budget_for(self, style, *, schema=True):
        planner, calls = _planner_with([_VALID])
        entry = {"thinking_style": style}
        with mock.patch(
            "services.llm_service._active_registry_entry", return_value=entry
        ):
            planner._call_llm_json(
                "u", "s", json_schema=_SCHEMA if schema else None,
            )
        return calls[0]

    def test_the_moe_style_plans_with_thinking_off_and_the_grammar_on(self):
        call = self._budget_for("gemma_prefix")

        self.assertEqual(call["thinking_budget"], 0)
        self.assertIs(call["enable_thinking"], False)
        self.assertEqual(call["json_schema"], _SCHEMA)

    def test_the_dense_gemma_style_plans_with_thinking_on(self):
        call = self._budget_for("gemma")

        self.assertEqual(call["thinking_budget"], 4096)
        self.assertNotIn("enable_thinking", call)
        self.assertNotIn("json_schema", call)

    def test_a_bounded_batch_of_a_long_timeline_keeps_shorter_reasoning(self):
        planner, calls = _planner_with([_VALID])
        with mock.patch(
            "services.llm_service._active_registry_entry",
            return_value={"thinking_style": "gemma"},
        ):
            planner._call_llm_json("u", "s", bounded=True)

        self.assertEqual(calls[0]["thinking_budget"], 2048)

    def test_an_unknown_style_still_gets_the_conservative_default(self):
        call = self._budget_for("something_new")

        self.assertEqual(call["thinking_budget"], 0)
        self.assertIs(call["enable_thinking"], False)


class TheCameraDecisionsAreAskedForTests(unittest.TestCase):
    def _planner_source(self):
        with open(
            os.path.join(_APP_DIR, "services", "director", "planners", "music_video.py"),
            "r", encoding="utf-8",
        ) as handle:
            return handle.read()

    def test_the_shot_schema_allows_the_whole_camera_plan(self):
        from services.director.planners import music_video

        camera = music_video._MUSIC_SHOT_PROPERTIES["camera_plan"]

        for field in ("framing", "angle", "movement", "movement_intensity", "lens_feel"):
            self.assertIn(field, camera["properties"])

    def test_the_output_example_shows_the_angle_and_the_lens(self):
        """The example is what the model copies; the measured 0-of-30 came from here."""

        source = self._planner_source()

        self.assertIn('"angle": "eye level"', source)
        self.assertIn('"lens_feel": "50mm, shallow"', source)

    def test_the_guide_states_the_camera_rule(self):
        with open(
            os.path.join(_GUIDES, "maestro_staged_progression_rules.md"),
            "r", encoding="utf-8",
        ) as handle:
            guide = handle.read()

        self.assertIn("angle", guide)
        self.assertIn("lens_feel", guide)
        self.assertIn("CAMERA", guide)

    def test_the_guide_states_the_per_shot_continuity_criterion(self):
        """Continuity is chosen where the idea asks for it, and the plan is where it shows."""

        with open(
            os.path.join(_GUIDES, "maestro_staged_progression_rules.md"),
            "r", encoding="utf-8",
        ) as handle:
            guide = handle.read()

        self.assertIn("CONTINUITY IS CHOSEN SHOT BY SHOT", guide)
        self.assertIn("independent", guide)


if __name__ == "__main__":
    unittest.main()
