"""Renumbering a shot's cast to the project's SUBJECT LOCK, and nothing else.

The repair has to be exact: it rewrites what every one of those shots renders. These tests pin
the two ways the first attempt went wrong -- rebuilding the entry from capture groups (which
dropped the colon and the spacing, producing ``<Subject 2>(2)Ricardo``) and comparing only the
text outside the cast block (which let a rewrite that deleted a blank separator join two
Context-IR fields pass as safe).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from services.director.prompt_normalize import (  # noqa: E402
    normalize_clip_prompts,
    normalize_subject_block,
    subject_label_skeleton,
)

CONTEXT = (
    "SUBJECT LOCK (critico, no negociable):\n"
    "- <Subject 1> es SIEMPRE Valeria. <Subject 2> es SIEMPRE Ricardo.\n"
    "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
)

TAIL = (
    "\n\nsummary: [reference generation] Opening composition: Medium close-up.\n\n"
    "retention_analysis: Preserve the identities.\n"
)


class RenumberTests(unittest.TestCase):
    def test_a_swapped_pair_is_renumbered_by_who_it_describes(self):
        text = (
            "subject_definitions: <Subject 1> (S1): Ricardo , leaning into the frame.\n"
            "<Subject 2> (S2): Valeria , listening from the side." + TAIL
        )

        fixed = normalize_subject_block(text, CONTEXT)

        self.assertIn("<Subject 2> (S2): Ricardo , leaning into the frame.", fixed)
        self.assertIn("<Subject 1> (S1): Valeria , listening from the side.", fixed)

    def test_only_the_numbers_move(self):
        text = (
            "subject_definitions: <Subject 1> (S1): Ricardo , leaning into the frame.\n"
            "<Subject 2> (S2): Valeria , listening from the side." + TAIL
        )

        fixed = normalize_subject_block(text, CONTEXT)

        self.assertEqual(subject_label_skeleton(fixed), subject_label_skeleton(text))

    def test_the_separator_between_fields_survives(self):
        text = (
            "subject_definitions: <Subject 1> (S1): Ricardo, wearing a sweater.\n"
            "<Subject 2> (S2): Valeria, in a blazer.\n\n"
            "summary: a shot.\n\nretention_analysis: keep it.\n"
        )

        fixed = normalize_subject_block(text, CONTEXT)

        self.assertIn("\n\nsummary:", fixed)
        self.assertIn("summary: a shot.\n\nretention_analysis:", fixed)

    def test_the_dialect_without_a_speaker_label_is_handled(self):
        """``<Subject 1>: Ricardo`` carries no (Sx) at all.

        This dialect was invisible to the first pattern, so those shots kept their swapped
        numbering after a repair that reported success.
        """

        text = (
            "subject_definitions: <Subject 1>: Ricardo, wearing a charcoal sweater.\n"
            "<Subject 2>: Valeria, in a navy blazer." + TAIL
        )

        fixed = normalize_subject_block(text, CONTEXT)

        self.assertIn("<Subject 2>: Ricardo, wearing a charcoal sweater.", fixed)
        self.assertIn("<Subject 1>: Valeria, in a navy blazer.", fixed)

    def test_a_participant_the_lock_does_not_name_is_left_alone(self):
        text = (
            "subject_definitions: <Subject 1> (S1): Ana, wearing a sweater.\n"
            "<Subject 2> (S2): The waiter, holding a tray." + TAIL
        )

        self.assertEqual(normalize_subject_block(text, CONTEXT), text)

    def test_a_project_without_a_lock_is_never_rewritten(self):
        text = "subject_definitions: <Subject 1> (S1): Ricardo, wearing a sweater." + TAIL

        self.assertEqual(normalize_subject_block(text, "no rules here"), text)

    def test_an_entry_that_names_nobody_is_not_renumbered(self):
        """``<Subject 2>: facial, bodily, and character identity come from <Picture 2>``

        There is no participant to look up, so the number cannot be decided from this text
        alone. Guessing here would attach a face to the wrong person.
        """

        text = (
            "subject_definitions: <Subject 1> (S1): Valeria, in a navy blazer.\n"
            "<Subject 2>: facial, bodily, and character identity come from <Picture 2>." + TAIL
        )

        fixed = normalize_subject_block(text, CONTEXT)

        self.assertEqual(fixed, text)


class ClipFieldsTests(unittest.TestCase):
    def test_both_saved_texts_are_offered_when_both_carry_the_cast(self):
        prompt = "subject_definitions: <Subject 1> (S1): Ricardo, in a sweater." + TAIL
        clip = {
            "video_prompt": prompt,
            "_director_h3_source_prompt": prompt,
            "_director_project_context": CONTEXT,
        }

        changed = normalize_clip_prompts(clip)

        self.assertEqual(set(changed), {"video_prompt", "_director_h3_source_prompt"})
        for text in changed.values():
            self.assertIn("<Subject 2> (S2): Ricardo", text)

    def test_a_shot_with_no_project_context_is_left_alone(self):
        clip = {"video_prompt": "subject_definitions: <Subject 1> (S1): Ricardo."}

        self.assertEqual(normalize_clip_prompts(clip), {})


if __name__ == "__main__":
    unittest.main()
