"""An image reference that declares nothing is a place, not a person.

Measured on a real project: a location photograph the user had uploaded himself arrived with no
intent and no character behind it, so the manifest's "identity" default turned it into a person in
the compiled prompt -- "<Subject 3> is the supplied image reference, whose facial, bodily, and
character identity come from <Picture 3>" with "fully_preserved" retention -- and the render cast
a woman nobody had written, wearing the wardrobe the prompt described for the actual lead.

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

from services.director.reference_intents import (  # noqa: E402
    default_untyped_reference_intents,
)


class AnUntypedUploadBecomesThePlaceItShowsTests(unittest.TestCase):
    def test_a_bare_image_is_a_scene_reference(self):
        """The shape measured in the field: an upload with no intent and no role."""

        references = [{"type": "image", "path": "bodega.png", "role": ""}]

        normalized = default_untyped_reference_intents(references)

        self.assertEqual(normalized[0]["image_intent"], "scene")
        self.assertTrue(normalized[0]["role"])

    def test_a_bare_image_with_no_type_at_all_is_still_an_image(self):
        normalized = default_untyped_reference_intents([{"path": "bodega.png"}])

        self.assertEqual(normalized[0]["image_intent"], "scene")

    def test_it_does_not_mutate_the_caller_s_list(self):
        references = [{"type": "image", "path": "bodega.png"}]

        default_untyped_reference_intents(references)

        self.assertNotIn("image_intent", references[0])


class ADeclaredMeaningIsNeverOverriddenTests(unittest.TestCase):
    def test_a_library_character_stays_an_identity(self):
        """A character is the case the identity default exists for."""

        references = [
            {
                "type": "image",
                "path": "martha.png",
                "role": "Martha (S1)",
                "library_character_id": "65e676c0b9fd42be",
            }
        ]

        normalized = default_untyped_reference_intents(references)

        self.assertNotIn("image_intent", normalized[0])

    def test_a_character_name_alone_is_enough(self):
        normalized = default_untyped_reference_intents(
            [{"type": "image", "path": "roman.png", "character_name": "Roman (S2)"}]
        )

        self.assertNotIn("image_intent", normalized[0])

    def test_a_declared_intent_is_kept(self):
        for intent in ("identity", "composition", "style", "object", "scene"):
            with self.subTest(intent=intent):
                normalized = default_untyped_reference_intents(
                    [{"type": "image", "path": "x.png", "image_intent": intent}]
                )

                self.assertEqual(normalized[0]["image_intent"], intent)

    def test_a_declared_role_is_kept(self):
        normalized = default_untyped_reference_intents(
            [{"type": "image", "path": "x.png", "role": "the far wall"}]
        )

        self.assertEqual(normalized[0]["role"], "the far wall")

    def test_audio_and_video_references_are_untouched(self):
        references = [
            {"type": "audio", "path": "song.wav", "audio_intent": "drive"},
            {"type": "video", "path": "clip.mp4"},
        ]

        normalized = default_untyped_reference_intents(references)

        self.assertEqual(normalized, references)


class ItIsSafeOnOddInputTests(unittest.TestCase):
    def test_a_missing_or_odd_value_comes_back_unchanged(self):
        self.assertIsNone(default_untyped_reference_intents(None))
        self.assertEqual(default_untyped_reference_intents([]), [])
        self.assertEqual(default_untyped_reference_intents("nonsense"), "nonsense")

    def test_a_non_mapping_entry_is_kept(self):
        self.assertEqual(default_untyped_reference_intents(["plain"]), ["plain"])


class ThePipelineUsesItTests(unittest.TestCase):
    def test_the_director_manifest_reads_the_submitted_references_through_it(self):
        with open(
            os.path.join(_APP_DIR, "services", "director_pipeline.py"),
            "r", encoding="utf-8",
        ) as handle:
            source = handle.read()

        self.assertIn("default_untyped_reference_intents(", source)
        self.assertNotIn(
            'submitted_references = params.get("minimax_h3_references")', source
        )


if __name__ == "__main__":
    unittest.main()
