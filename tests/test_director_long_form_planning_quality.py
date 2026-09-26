"""Long timelines keep their variety, and the batch work is sized for the model.

Measured on a real 16-clip project with the small fast model: the first clips were
inventive and from the middle on the planner repeated the same staging ("it only
walks down the street"). Three things in the planner produced that, and this module
pins the three fixes:

1. A batch of a long timeline forced ``thinking_budget=0``, removing exactly the help
   ``base.py`` says a small Gemma needs ("Without thinking, smaller Gemma models like
   4B miss these rules under cognitive load"). A bounded batch now gets a shorter
   budget instead of none.
2. The batch size was 12 for every model, and a 4B model degrades inside its own long
   answer: the later items collapse onto the staging the earlier ones just wrote.
   Small models now plan 8.
3. A batch saw only its own clips, with the previous ending as its only reference. It
   now receives the whole timeline as context, so it can see what is still ahead.

It also pins that a batch which comes back short says so, because a deterministically
filled clip and a clip the model had no ideas for used to look identical.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import llm_service  # noqa: E402
from services.director.planners.music_video import MusicVideoPlanner  # noqa: E402
from services.director.planners.short_film import ShortFilmPlanner  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SMALL_MODEL = {"name": "Abhiray/gemma-4-E4B-it-heretic-GGUF", "thinking_style": "gemma"}
LARGE_MODEL = {"name": "Abhiray/qwen-3.6-14B", "thinking_style": "gemma"}


def _read(*parts: str) -> str:
    return (ROOT / "app/services/director/planners" / Path(*parts)).read_text(encoding="utf-8")


class BatchSizeTests(unittest.TestCase):
    def test_a_small_model_gets_shorter_batches(self):
        with patch.object(llm_service, "_active_registry_entry", return_value=SMALL_MODEL):
            self.assertEqual(MusicVideoPlanner.long_form_batch_size(), 8)
            self.assertEqual(ShortFilmPlanner.long_form_batch_size(), 8)

    def test_a_larger_model_keeps_the_full_batch(self):
        with patch.object(llm_service, "_active_registry_entry", return_value=LARGE_MODEL):
            self.assertEqual(MusicVideoPlanner.long_form_batch_size(), 12)

    def test_an_unknown_runtime_falls_back_to_the_previous_size(self):
        # No registry entry is the state every planner test runs in, and the long-form
        # tests there assert batches of 12.
        with patch.object(llm_service, "_active_registry_entry", return_value=None):
            self.assertEqual(MusicVideoPlanner.long_form_batch_size(), 12)
        with patch.object(llm_service, "_active_registry_entry", side_effect=RuntimeError("no runtime")):
            self.assertEqual(MusicVideoPlanner.long_form_batch_size(), 12)

    def test_both_planners_size_their_batches_for_the_model(self):
        for source in (_read("music_video.py"), _read("short_film.py")):
            with self.subTest(source=source[:40]):
                self.assertIn("self.long_form_batch_size()", source)
                self.assertNotIn("batch_size=self._LONG_FORM_BATCH_SIZE", source)
                self.assertNotIn("batch_size=12,", source)


class ReasoningBudgetTests(unittest.TestCase):
    def _recorded_budget(self, bounded: bool, model: dict) -> dict:
        calls: list[dict] = []

        def generate(**kwargs):
            calls.append(kwargs)
            return json.dumps([{"scene_goal": "one"}])

        planner = MusicVideoPlanner(llm_generate=generate)
        with patch.object(llm_service, "_active_registry_entry", return_value=model):
            planner._call_llm_json(
                user_prompt="plan", system_prompt="system", bounded=bounded,
            )
        self.assertTrue(calls, "the model was never called")
        return calls[0]

    def test_a_bounded_batch_keeps_reasoning_for_a_small_gemma(self):
        call = self._recorded_budget(True, SMALL_MODEL)
        self.assertEqual(call["thinking_budget"], 2048)
        self.assertNotIn("enable_thinking", call)

    def test_an_unbounded_plan_keeps_the_full_budget(self):
        call = self._recorded_budget(False, SMALL_MODEL)
        self.assertEqual(call["thinking_budget"], 4096)

    def test_qwen_keeps_thinking_off_in_both_cases(self):
        for bounded in (True, False):
            with self.subTest(bounded=bounded):
                call = self._recorded_budget(bounded, {"name": "Qwen3.6", "thinking_style": "qwen"})
                self.assertEqual(call["thinking_budget"], 0)
                self.assertFalse(call["enable_thinking"])

    def test_both_planners_mark_their_bounded_batches(self):
        for source in (_read("music_video.py"), _read("short_film.py")):
            with self.subTest(source=source[:40]):
                self.assertNotIn("0 if kwargs.get(\"_bounded_music_batch\") else 4096", source)
                self.assertIn("bounded=", source)


class WholeTimelineContextTests(unittest.TestCase):
    def test_a_batch_is_shown_the_whole_timeline(self):
        music = _read("music_video.py")
        self.assertIn("THE WHOLE TIMELINE (context only: see where this batch sits ", music)
        # The batch's own clips are still the ones it plans; the overview is context.
        self.assertIn("+ \"\\n\".join(clip_contexts)", music)
        self.assertIn("plan ONLY clips {start + 1}-{end})", music)

    def test_the_film_path_builds_its_overview_from_the_clip_timeline(self):
        film = _read("short_film.py")
        self.assertIn("timeline_overview = \"\\n\".join(", film)
        self.assertIn("f\"Clip {index + 1}: ", film)
        self.assertIn("timeline_overview}", film)


class DeterministicFillTests(unittest.TestCase):
    def test_a_filled_batch_says_so(self):
        base = _read("base.py")
        self.assertIn("filled = 0", base)
        self.assertIn("were filled deterministically because the model's", base)
        self.assertIn("those clips are generic.", base)


if __name__ == "__main__":
    unittest.main()
