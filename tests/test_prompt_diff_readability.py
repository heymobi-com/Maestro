"""The prompt comparison is readable, and the reader controls its size.

Measured complaint: "the fields where it compares Actual and Propuesta get hidden
below the rest of the text, with no way to resize the area, so I cannot read the
texts; sometimes it shows them, sometimes it hides them".

Two causes, both in the markup:

1. The box holding the two columns was a flex child with ``min-h-0``, so it held
   whatever height was left over. A long reading above it, a short window, or a
   screen full of regions collapsed it to nothing.
2. The only resize affordance was the window's own 16-pixel corner, drawn as a
   faint gradient and marked ``aria-hidden``: a reader had no reason to know it
   was there, and it resized the window rather than the comparison.

The box now has a floor it cannot go below, a labelled grab bar that resizes it,
a remembered height per shot, and a stacked layout for narrow windows.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return (ROOT / "ui/src" / Path(*parts)).read_text(encoding="utf-8")


class CompareBoxTests(unittest.TestCase):
    def setUp(self):
        self.view = _read("components", "DirectorDashboard", "PromptDiffView.tsx")

    def test_the_box_cannot_be_squeezed_to_nothing(self):
        self.assertIn("const MIN_COMPARE_PX = 240", self.view)
        self.assertIn('className="flex min-h-[240px] flex-col rounded border border-border bg-bg-tertiary/40"', self.view)
        # The collapsed shape is gone: it was the flex child that lost every
        # height contest it entered.
        self.assertNotIn('className="flex-1 min-h-0 flex flex-col rounded border border-border', self.view)

    def test_the_comparison_is_resized_by_its_own_handle(self):
        self.assertIn("const startResize = (event: React.PointerEvent) => {", self.view)
        self.assertIn("onPointerDown={startResize}", self.view)
        self.assertIn('aria-label="Cambiar la altura de la comparación"', self.view)
        self.assertIn("cursor-ns-resize", self.view)
        # Dragging up past the floor stops at the floor instead of hiding the text.
        self.assertIn("Math.max(MIN_COMPARE_PX, Math.round(startHeight + move.clientY - startY))", self.view)

    def test_the_height_and_layout_come_back_for_the_same_shot(self):
        self.assertIn("const HEIGHT_KEY = 'maestro.promptDiff.height.'", self.view)
        self.assertIn("const STACKED_KEY = 'maestro.promptDiff.stacked.'", self.view)
        self.assertIn("loadNumber(HEIGHT_KEY + (persistKey || ''), DEFAULT_COMPARE_PX)", self.view)
        # Persisted load/save must never throw a comparison away.
        self.assertIn("Number.isFinite(stored) && stored >= MIN_COMPARE_PX", self.view)
        self.assertIn("// A full or blocked storage must not stop the comparison from working.", self.view)

    def test_a_narrow_window_can_stack_the_two_sides(self):
        self.assertIn("const toggleStacked = () => setStacked(value => {", self.view)
        self.assertIn(">{stacked ? 'Lado a lado' : 'Apilado'}</button>", self.view)
        # Stacked, each side carries its own label, because the shared header row
        # is gone in that layout.
        self.assertIn("const column = (region: DiffRegion, side: 'left' | 'right', oneAbove: boolean) => (", self.view)
        self.assertIn("{side === 'left' ? 'Actual' : 'Propuesta'}", self.view)
        self.assertIn("{!stacked && (", self.view)

    def test_the_words_stay_at_reading_size(self):
        self.assertIn("text-[13px] leading-relaxed", self.view)
        self.assertNotIn("text-[10px]", self.view)


class DashboardWiringTests(unittest.TestCase):
    def test_the_comparison_is_given_the_shot_it_belongs_to(self):
        dashboard = _read("components", "DirectorDashboard", "DirectorDashboard.tsx")
        self.assertIn("persistKey={`prompt-${pipeline.pipeline_id}-${clip.index}`}", dashboard)


if __name__ == "__main__":
    unittest.main()
