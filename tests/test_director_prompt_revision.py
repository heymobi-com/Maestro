"""Correcting one shot's prompt from a plain-language note, with the LLM.

Hand-editing a compiled H3 prompt is impractical, and saying what is wrong is
what a director actually knows. The note is combined with the adjacent shots so
the rewrite stays continuous, and the result is returned for review rather than
saved, because the rewrite is a suggestion.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services import director_pipeline as pipeline  # noqa: E402

_UI_DIR = os.path.abspath(os.path.join(_HERE, "..", "ui", "src"))


def _read(*parts: str) -> str:
    with open(os.path.join(_UI_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


class PromptRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out_dir = self.temp.name
        self.pid = "revision01"

    def _save(self, clips):
        state = {
            "pipeline_id": self.pid,
            "status": "completed",
            "video_model": "minimax_h3_ref2va_fused_turbo",
            "clips": clips,
            "_params_snapshot": {"scene_description": "PROJECT: a studio"},
        }
        with open(
            os.path.join(self.out_dir, f"_director_pipeline_{self.pid}.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(state, handle)
        return state

    def _stub_llm(self, answer):
        """Patch the model load and the completion, and return what was sent."""

        captured = {}

        def fake_enhance(**kwargs):
            captured.update(kwargs)
            return answer

        loader = patch.object(pipeline, "_ensure_llm_loaded", lambda params: None)
        completion = patch("services.llm_service.enhance_prompt", fake_enhance)
        loader.start()
        completion.start()
        self.addCleanup(loader.stop)
        self.addCleanup(completion.stop)
        return captured

    def test_a_note_is_required(self):
        self._save([{"index": 0, "video_prompt": "a shot"}])

        with self.assertRaises(ValueError) as caught:
            pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "   ")

        self.assertIn("Describe what should be corrected", str(caught.exception))

    def test_an_out_of_range_shot_is_refused(self):
        self._save([{"index": 0, "video_prompt": "a shot"}])

        with self.assertRaises(ValueError):
            pipeline.revise_clip_prompt(self.out_dir, self.pid, 7, "fix it")

    def test_a_missing_pipeline_is_refused(self):
        with self.assertRaises(ValueError):
            pipeline.revise_clip_prompt(
                self.out_dir, "nosuchrun", 0, "fix it",
            )

    def test_the_llm_is_loaded_before_it_is_called(self):
        # It used to call enhance_prompt with nothing loaded, which failed with
        # "LLM not loaded. Call load_model() first."
        self._save([{"index": 0, "video_prompt": "a shot body"}])
        calls = []
        with patch.object(
            pipeline, "_ensure_llm_loaded", lambda params: calls.append(params),
        ):
            with patch("services.llm_service.enhance_prompt", lambda **kw: "rewritten"):
                pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "fix it")

        self.assertEqual(len(calls), 1)

    def test_the_note_and_neighbours_reach_the_model(self):
        self._save([
            {"index": 0, "video_prompt": "first shot body"},
            {"index": 1, "video_prompt": "middle shot body"},
            {"index": 2, "video_prompt": "last shot body"},
        ])
        captured = self._stub_llm("corrected shot body")

        result = pipeline.revise_clip_prompt(
            self.out_dir, self.pid, 1,
            "the line belongs to the man, not the woman",
        )

        sent = captured["prompt"]
        self.assertIn("the line belongs to the man", sent)
        self.assertIn("middle shot body", sent)
        # Continuity comes from both neighbours, not just the previous shot.
        self.assertIn("previous shot 1: first shot body", sent)
        self.assertIn("next shot 3: last shot body", sent)
        self.assertEqual(result["clip_index"], 1)
        # The answer is now asked for as a reading plus a rewrite. Plain prose
        # carries no FIXED_PROMPT, so nothing is handed over to the editor: a
        # stray sentence must never replace a shot.
        self.assertEqual(result["video_prompt"], "")
        self.assertFalse(result["rewritten"])

    def test_the_system_prompt_forbids_reassigning_dialogue(self):
        system = pipeline._REVISE_PROMPT_SYSTEM

        self.assertIn("<d>", system)
        self.assertIn("Never re-word, re-assign or drop a line", system)
        self.assertIn("Change only what the note requires", system)
        # "Change only what the note asks for" used to protect the extra shots:
        # a note about the framing left [Shot 2] alone. The exception is explicit
        # now, and a [Shot 2] is the first thing the gate refuses.
        self.assertIn("does not protect a second shot", system)
        self.assertIn("exactly one [Shot 1] marker", system)
        self.assertEqual(system, pipeline._REVISE_PROMPT_SYSTEM)

    def test_the_revision_is_not_saved(self):
        self._save([{"index": 0, "video_prompt": "original body"}])
        self._stub_llm("rewritten body")

        pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "make it warmer")

        reloaded = pipeline.load_pipeline_state(self.out_dir, self.pid)
        self.assertEqual(reloaded["clips"][0]["video_prompt"], "original body")

    def test_a_model_that_returns_nothing_is_an_error_not_a_blank_prompt(self):
        self._save([{"index": 0, "video_prompt": "original body"}])
        self._stub_llm("  ")

        with self.assertRaises(ValueError) as caught:
            pipeline.revise_clip_prompt(self.out_dir, self.pid, 0, "fix it")

        self.assertIn("returned no answer", str(caught.exception))


class PromptRevisionWiringTests(unittest.TestCase):
    """The panel must expose the box and the button that reach that endpoint."""

    def test_the_api_exposes_the_revision_endpoint(self):
        with open(
            os.path.join(_APP_DIR, "launch.py"), encoding="utf-8",
        ) as handle:
            launch = handle.read()

        self.assertIn(
            '"/api/v1/director/pipelines/{pid}/clips/{clip_index}/revise-prompt"',
            launch,
        )
        self.assertIn("revise_clip_prompt(", launch)

    def test_the_client_calls_that_endpoint(self):
        client = _read("api", "client.ts")

        self.assertIn("export async function reviseClipPrompt(", client)
        self.assertIn("/revise-prompt`", client)

    def test_the_panel_has_the_correction_box_and_the_button(self):
        dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )

        self.assertIn("Correct this shot", dashboard)
        self.assertIn("Fix with AI", dashboard)
        self.assertIn("reviseClipPrompt(", dashboard)

    def test_the_rewrite_is_compared_before_it_reaches_the_editor(self):
        dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )
        start = dashboard.index("const runFixWithAi = async () => {")
        body = dashboard[start:dashboard.index("}", dashboard.index("setFixing(false)", start))]

        self.assertIn("setFixAnswer(answer)", body)
        self.assertNotIn("onSavePrompt", body)
        # Only a rewrite that survived the gate is offered. A refused one arrives
        # with its errors and no proposal, so a refusal cannot look like a fix.
        self.assertIn("answer.rewritten && answer.video_prompt", body)
        # Offered as a comparison instead of being swapped into the editor: whether
        # a correction changed the acting instruction or only the prose around it is
        # the only thing worth accepting it on, and swapping it in silently made a
        # refused suggestion look applied.
        self.assertNotIn("setEditVideoPrompt(answer.video_prompt)", body)
        self.assertIn("setPromptView('diff')", body)


class PromptComparisonTests(unittest.TestCase):
    """The proposal is read next to the prompt it changes."""

    def setUp(self):
        self.dashboard = _read(
            "components", "DirectorDashboard", "DirectorDashboard.tsx",
        )
        self.view = _read(
            "components", "DirectorDashboard", "PromptDiffView.tsx",
        )
        self.diff = _read("lib", "promptDiff.ts")

    def test_the_comparison_marks_added_and_removed_text(self):
        self.assertIn("bg-chip-red/20", self.view)
        self.assertIn("bg-accent-green/20", self.view)
        # Colour alone would not survive a dim screen, so removals are struck
        # through as well.
        self.assertIn("line-through", self.view)
        self.assertIn("Actual", self.view)
        self.assertIn("Propuesta", self.view)

    def test_the_comparison_says_how_much_changed(self):
        self.assertIn("cambio(s)", self.view)
        self.assertIn("caracteres", self.view)
        self.assertIn("La propuesta no cambia nada", self.view)

    def test_the_unchanged_head_and_tail_are_trimmed_before_comparing(self):
        # That is what keeps a 4,000-character prompt cheap to compare.
        self.assertIn("leftTokens[head] === rightTokens[head]", self.diff)
        self.assertIn("leftTokens[leftEnd - 1] === rightTokens[rightEnd - 1]", self.diff)
        self.assertIn("MAX_CELLS", self.diff)

    def test_nothing_reaches_the_editor_until_it_is_applied(self):
        self.assertIn("const applyProposal = () => {", self.dashboard)
        self.assertIn("applied={editVideoPrompt === proposalPrompt}", self.dashboard)
        self.assertIn("Aplicar al editor", self.view)

    def test_the_assistant_is_reachable_from_the_prompt_window(self):
        start = self.dashboard.index("storageKey={`prompt-")
        window = self.dashboard[start:self.dashboard.index("</FloatingPanel>", start)]

        self.assertIn("Asistente IA", window)
        self.assertIn("Corregir con IA", window)
        self.assertIn("runFixWithAi", window)
        self.assertIn("Ver comparación", window)


if __name__ == "__main__":
    unittest.main()
