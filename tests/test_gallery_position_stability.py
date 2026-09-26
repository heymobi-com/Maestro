"""Nothing in the gallery scrolls itself away from where the user is reading.

Reported from use: "I am on 21 and it jumps to 75 or 140, and I have to go back
and find where I made my correction". Two things did that. The thumbnail strip
followed the active index, and the active index is not stable: the feed
re-selects whichever card sits on the viewport centre, and every generation
prepends a video, which moves every index down by one. And the feed keyed its
measured heights by POSITION, so after a prepend every stored height described a
different video, while a length change also dropped all of them back to the
width-based estimate -- thousands of pixels of error over a real library.

These cases are ours, so they live in their own file: the gallery tests they used
to sit in belong to upstream, and every line we add there is a line that can
conflict.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GalleryStaysWhereTheUserIsTests(unittest.TestCase):
    def _read(self, *parts):
        return (ROOT.joinpath("ui", "src", *parts)).read_text(encoding="utf-8")

    def test_the_thumbnail_strip_no_longer_scrolls_itself(self):
        strip = self._read("components", "MainContent", "ThumbnailGallery.tsx")

        self.assertNotIn("isAutoScrolling", strip)
        self.assertNotIn("behavior: 'smooth'", strip)
        # The effect that centred the active thumbnail is gone, not merely muted.
        self.assertNotIn("itemTop - viewHeight / 2", strip)

    def test_feed_heights_are_keyed_by_identity_and_survive_a_prepend(self):
        main = self._read("components", "MainContent", "MainContent.tsx")

        self.assertIn("useRef<Map<string, number>>(new Map())", main)
        self.assertIn("const heightKey = useCallback((index: number) => {", main)
        self.assertIn("return file ? outputIdentity(file) : `index:${index}`", main)
        self.assertIn("measuredHeights.current.get(heightKey(index))", main)
        self.assertIn("measuredHeights.current.set(key, height)", main)
        # An unconditional clear is what reset the whole list on every generation.
        self.assertIn("if (outputs.length === 0) measuredHeights.current.clear()", main)
        self.assertNotIn("  useEffect(() => {\n    measuredHeights.current.clear()", main)

    def test_the_reading_position_is_held_across_a_change_to_the_list(self):
        main = self._read("components", "MainContent", "MainContent.tsx")

        self.assertIn("const lastLayout = useRef<{ outputs: OutputFile[]; offsets: number[] }>", main)
        self.assertIn("const previous = lastLayout.current", main)
        self.assertIn("const desired = itemOffsets[nextIndex] + delta", main)
        self.assertIn("if (Math.abs(feedEl.scrollTop - desired) > 1) feedEl.scrollTop = desired", main)
        # An explicit click still owns the scroll while it is in flight.
        self.assertIn(
            "if (!feedEl || scrollTargetIndex.current !== null) return",
            main,
        )


if __name__ == "__main__":
    unittest.main()
