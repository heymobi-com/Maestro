"""A one-shot body must not carry a timestamp that reads as a cut inside it.

Measured on a real 30-shot project, in the prompts the engine actually rendered: 11 clips carried
one loose marker each -- ``At MM:04.000``, ``MM:08.200``, ``MM:12.400``, about four seconds apart
-- while those clips sit at 88 s, 94 s and 99 s of the film. Neither compiler step reached them:
the rewrite that aligns cut markers only matches the ``[Shot N] At ...`` shape, and the collapse
that enforces one shot per body only has something to remove when there is a marker.

A timestamp four seconds into a seven-second clip tells the renderer a second shot starts there,
which is how one shot becomes a fast succession of everything the film was supposed to spread out.

These cases are ours, so they live in their own file.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_single_shot_times import strip_loose_cut_times  # noqa: E402


class TheLooseMarkerGoesTests(unittest.TestCase):
    def test_the_marker_and_its_separator_both_go(self):
        """The shape measured in the field: a definition block, then a loose marker."""

        body = (
            "<Subject 1> (S1): facial, bodily, and character identity come from "
            "<Picture 1>. At MM:04.000, Roman stands in the warehouse, rain falling "
            "through a shaft of light."
        )

        cleaned, removed = strip_loose_cut_times(body)

        self.assertEqual(removed, 1)
        self.assertNotIn("At MM:04.000", cleaned)
        self.assertIn("<Picture 1>. Roman stands in the warehouse", cleaned)
        self.assertNotIn("  ", cleaned)
        self.assertNotIn(",.", cleaned)

    def test_every_measured_value_is_removed(self):
        """The eleven markers one real project carried, one clip each."""

        for value in (
            "MM:04.000", "MM:08.200", "MM:12.400", "MM:16.200", "MM:19.400", "MM:23.800",
            "MM:27.100", "MM:31.500", "MM:36.400", "MM:40.900", "MM:45.300",
        ):
            with self.subTest(value=value):
                cleaned, removed = strip_loose_cut_times(f"Shot body. At {value}, she turns.")

                self.assertEqual(removed, 1)
                self.assertNotIn(value, cleaned)
                self.assertIn("she turns", cleaned)

    def test_a_body_without_one_is_left_byte_for_byte(self):
        body = "[Shot 1] Martha stands centre frame, rain falling through a shaft of light."

        cleaned, removed = strip_loose_cut_times(body)

        self.assertEqual(removed, 0)
        self.assertEqual(cleaned, body)

    def test_an_empty_or_missing_body_is_safe(self):
        self.assertEqual(strip_loose_cut_times(""), ("", 0))
        self.assertEqual(strip_loose_cut_times(None), ("", 0))


class OrdinaryTimingProseIsLeftAloneTests(unittest.TestCase):
    """Only the official marker shape is a section label; the rest is somebody's note."""

    def test_a_seconds_note_survives(self):
        body = "The rain starts at 3.5s into the shot and the light falls with it."

        cleaned, removed = strip_loose_cut_times(body)

        self.assertEqual(removed, 0)
        self.assertEqual(cleaned, body)

    def test_a_duration_is_not_a_marker(self):
        body = "She walks for 00:04 and then stops."

        cleaned, removed = strip_loose_cut_times(body)

        self.assertEqual(removed, 0)
        self.assertEqual(cleaned, body)


class TheCompilerUsesItTests(unittest.TestCase):
    def test_both_compile_paths_strip_the_marker(self):
        """The ref2va body and the compiled body both have to pass through it."""

        with open(
            os.path.join(_APP_DIR, "services", "director", "h3_dialogue.py"),
            "r", encoding="utf-8",
        ) as handle:
            source = handle.read()

        self.assertEqual(source.count("strip_loose_cut_times(_single_shot_body("), 2)
        self.assertIn(
            "from services.director.h3_single_shot_times import strip_loose_cut_times",
            source,
        )


if __name__ == "__main__":
    unittest.main()
