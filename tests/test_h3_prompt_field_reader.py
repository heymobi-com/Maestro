"""The field reader and the field counter have to read a prompt the same way.

`validate_h3_prompt_contract` counts a Context-IR field at a line start
(`^\s*detailed_description\s*:`), and then inspects the value `_extract_h3_fields` returned for
that same name. The reader searched without the anchor, so the two could disagree about a prompt
that has exactly one real field: a label mentioned in the prose earlier in the text won the
match, and the slice ended at the real label.

The refusal that produced, measured on a real single-shot project:

    Shot 1: detailed_description is missing [Shot 1]

on a prompt that carries `[Shot 1]` inside a `detailed_description` field the counter had just
counted as present exactly once. The prose mention is what a small local planner emits when the
scene description it was given is itself a compiled prompt: the user pasted a six-field prompt to
correct an earlier shot, and the model echoed a label while rewriting it.

So the cases here pin both halves: the reader prefers the declared field, and the legacy one-line
prompt -- the reason the search was unanchored in the first place -- still reads.
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

from services.director import h3_dialogue  # noqa: E402
from services.director import h3_prompt_shape  # noqa: E402

REF2VA = (
    "subject_definitions: <Subject 1> is Aria (S1): a woman in a red dress.\n\n"
    "summary: [reference generation] Aria turns from the window.\n\n"
    "retention_analysis: <Picture 1> (appears in [Shot 1]): fully_preserved - identity.\n\n"
    "detailed_description: The target video keeps a warm grade. "
    "[Shot 1] <Subject 1> turns from the window, and the room stays still.\n\n"
    "overall_soundscape: Quiet room tone.\n\n"
    "non_diegetic_music: N/A"
)


class TheFieldReaderMatchesTheFieldCounterTests(unittest.TestCase):
    def test_a_clean_prompt_reads_its_six_fields(self):
        fields = h3_dialogue._extract_h3_fields(REF2VA)

        self.assertEqual(
            sorted(fields),
            ["detailed_description", "non_diegetic_music", "overall_soundscape",
             "retention_analysis", "subject_definitions", "summary"],
        )
        self.assertIn("[Shot 1]", fields["detailed_description"])

    def test_a_prose_mention_does_not_win_the_field(self):
        """The reported refusal: a label echoed in the summary, before the real field."""

        echoed = REF2VA.replace(
            "summary: ",
            "summary: Rewritten from detailed_description: the framing is unchanged. ",
            1,
        )
        fields = h3_dialogue._extract_h3_fields(echoed)

        # The real field, not the two words that follow the mention.
        self.assertIn("[Shot 1]", fields["detailed_description"])
        self.assertIn("<Subject 1> turns from the window", fields["detailed_description"])
        self.assertTrue(fields["detailed_description"].startswith("The target video"))

    def test_the_refusal_that_reported_it_is_gone(self):
        echoed = REF2VA.replace(
            "summary: ",
            "summary: Rewritten from detailed_description: the framing is unchanged. ",
            1,
        )

        errors = h3_dialogue.validate_h3_prompt_contract(
            echoed, [], mode="ref2va", references=[{"type": "image"}],
        )

        self.assertNotIn("detailed_description is missing [Shot 1]", errors)

    def test_every_field_name_is_read_from_its_own_line(self):
        """Each of the six is a line in a compiled prompt; none may be shadowed by prose."""

        for name in h3_dialogue._H3_REF2VA_FIELDS:
            with self.subTest(field=name):
                shadowed = REF2VA.replace(
                    "subject_definitions: ",
                    f"subject_definitions: (no {name}: yet) ",
                    1,
                )
                fields = h3_dialogue._extract_h3_fields(shadowed)
                self.assertEqual(sorted(fields), sorted(h3_dialogue._H3_REF2VA_FIELDS))


class TheLegacyOneLinePromptStillReadsTests(unittest.TestCase):
    """Why the search was unanchored: prompts that predate the six-field form."""

    def test_an_inline_prompt_still_yields_its_fields(self):
        legacy = (
            "integrated_multimodal_description: [Shot 1] A man crosses the street. "
            "overall_soundscape: Traffic. non_diegetic_music: N/A"
        )

        fields = h3_dialogue._extract_h3_fields(legacy)

        self.assertEqual(fields["integrated_multimodal_description"],
                         "[Shot 1] A man crosses the street.")
        self.assertEqual(fields["overall_soundscape"], "Traffic.")
        self.assertEqual(fields["non_diegetic_music"], "N/A")

    def test_a_prompt_with_no_field_at_all_returns_nothing(self):
        self.assertEqual(h3_dialogue._extract_h3_fields("A man crosses the street."), {})


class ARefusalSaysWhatItSawTests(unittest.TestCase):
    """A refusal that only names the rule costs a whole planning pass to diagnose."""

    def test_it_names_every_field_and_its_length(self):
        line = h3_prompt_shape.describe(h3_dialogue._extract_h3_fields(REF2VA))

        self.assertIn("detailed_description=", line)
        self.assertIn("summary=", line)
        self.assertIn("Body starts:", line)

    def test_it_shows_where_the_body_begins(self):
        line = h3_prompt_shape.describe({"detailed_description": "The target video keeps a warm grade."})

        self.assertIn("The target video keeps a warm grade.", line)

    def test_a_prompt_with_no_field_says_so(self):
        line = h3_prompt_shape.describe({})

        self.assertIn("no Context-IR field at all", line)

    def test_the_refusal_prints_it(self):
        source = open(
            os.path.join(_APP_DIR, "services", "director", "h3_dialogue.py"),
            encoding="utf-8",
        ).read()

        self.assertIn("print(describe(_extract_h3_fields(prompt)))", source)


if __name__ == "__main__":
    unittest.main()
