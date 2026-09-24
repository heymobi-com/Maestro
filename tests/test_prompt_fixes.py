"""The corrections the assistant decides and the code applies.

The director's workflow is: watch the render, say what is wrong, read the diagnosis, have it
corrected. These tests pin both halves -- that each decision lands exactly where it should, and
that a decision the code cannot apply is refused with a reason instead of producing a prompt
nobody asked for (which is what made the assistant read as errors only).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from services.director.prompt_fixes import (  # noqa: E402
    PromptFixError,
    _content,
    apply_prompt_fixes,
    merge_subject_definitions,
    name_subject_entry,
    parse_prompt_fixes,
    set_line_speaker,
    set_subject_person,
)
from services.director.h3_dialogue import h3_dialogue_blocks, h3_restore_shot_marker  # noqa: E402
from services.director.prompt_normalize import subject_label_skeleton  # noqa: E402

FIELD_NAMES = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

CONTEXT = (
    "SUBJECT LOCK (critico, no negociable):\n"
    "- <Subject 1> es SIEMPRE Valeria. <Subject 2> es SIEMPRE Ricardo.\n"
)

PROMPT = (
    "subject_definitions: <Subject 1> (S1): Valeria, in a navy blazer.\n"
    "<Subject 2> (S2): Ricardo, in a charcoal sweater.\n\n"
    "summary: [reference generation] Two people talk.\n\n"
    "detailed_description: [Shot 1] The scene continues in the loft. "
    "(S1) is seen finishing her thought: <d>[Spanish] datos, a ordenes y a rendimientos.</d>"
    ". His voice carries wisdom as he says: <d>[Spanish] Baslyamente, un proyecto.</d>.\n\n"
    "overall_soundscape: Quiet room tone.\n\n"
    "non_diegetic_music: N/A\n"
)


class SpeakerFixTests(unittest.TestCase):
    def test_a_line_gets_the_speaker_the_director_asked_for(self):
        fixed, note = set_line_speaker(PROMPT, 2, 2)

        # The label goes where the renderer reads it: beside the line, not in the middle of the
        # prose it introduces.
        self.assertIn("(S2) <d>[Spanish] Baslyamente", fixed)
        self.assertIn("line 2 is now spoken by (S2)", note)

    def test_a_wrong_label_is_replaced_not_duplicated(self):
        fixed, _ = set_line_speaker(PROMPT, 1, 2)

        self.assertIn("(S2) is seen finishing her thought:", fixed)
        self.assertNotIn("(S1) is seen finishing her thought:", fixed)

    def test_only_the_cue_changes(self):
        fixed, _ = set_line_speaker(PROMPT, 2, 2)

        # A label is added where there was none, so the check is that the prose is untouched.
        self.assertEqual(_content(fixed), _content(PROMPT))
        self.assertNotEqual(_content(fixed), "")

    def test_a_line_that_does_not_exist_is_refused_with_its_number(self):
        with self.assertRaises(PromptFixError) as caught:
            set_line_speaker(PROMPT, 9, 2)

        self.assertIn("the shot has 2 spoken line(s)", str(caught.exception))


class CastFixTests(unittest.TestCase):
    def test_a_swapped_cast_is_renumbered(self):
        swapped = PROMPT.replace(
            "subject_definitions: <Subject 1> (S1): Valeria, in a navy blazer.\n"
            "<Subject 2> (S2): Ricardo, in a charcoal sweater.",
            "subject_definitions: <Subject 1> (S1): Ricardo, in a charcoal sweater.\n"
            "<Subject 2> (S2): Valeria, in a navy blazer.",
        )

        fixed, note = set_subject_person(swapped, 2, "Ricardo")

        self.assertIn("<Subject 2> (S2): Ricardo", fixed)
        self.assertIn("<Subject 1> (S1): Valeria", fixed)
        self.assertIn("<Subject 2> is Ricardo", note)

    def test_a_cast_that_is_already_right_is_reported_rather_than_rewritten(self):
        with self.assertRaises(PromptFixError) as caught:
            set_subject_person(PROMPT, 2, "Ricardo")

        self.assertIn("already numbers Ricardo", str(caught.exception))

    def test_an_entry_that_names_nobody_gets_its_name(self):
        prompt = PROMPT.replace(
            "<Subject 2> (S2): Ricardo, in a charcoal sweater.",
            "<Subject 2> (S2): facial, bodily, and character identity come from <Picture 2>.",
        )

        fixed, note = name_subject_entry(prompt, 2, "Ricardo")

        self.assertIn(
            "<Subject 2> (S2): Ricardo, facial, bodily, and character identity come from",
            fixed,
        )
        self.assertIn("<Subject 2> is Ricardo in the cast block", note)

    def test_an_entry_that_already_describes_someone_is_left_alone(self):
        with self.assertRaises(PromptFixError) as caught:
            name_subject_entry(PROMPT, 2, "Ricardo")

        self.assertIn("already describes a participant", str(caught.exception))


class MergeFixTests(unittest.TestCase):
    def test_two_cast_heads_become_one(self):
        prompt = PROMPT.replace(
            "\n<Subject 2> (S2): Ricardo, in a charcoal sweater.",
            "\nsubject_definitions: <Subject 2> (S2): Ricardo, in a charcoal sweater.",
        )

        fixed, note = merge_subject_definitions(prompt)

        self.assertEqual(len(fixed.split("subject_definitions")), 2)
        self.assertIn("<Subject 2> (S2): Ricardo", fixed)
        self.assertIn("1 extra cast head(s)", note)

    def test_the_fields_after_the_cast_block_survive(self):
        prompt = PROMPT.replace(
            "\n<Subject 2> (S2): Ricardo, in a charcoal sweater.",
            "\nsubject_definitions: <Subject 2> (S2): Ricardo, in a charcoal sweater.",
        )

        fixed, _ = merge_subject_definitions(prompt)

        self.assertIn("\n\nsummary:", fixed)
        self.assertIn("non_diegetic_music: N/A", fixed)
        # The block's own label is what gets folded away, so the numbered skeleton is the wrong
        # comparison here; what must survive is every field and every spoken line.
        self.assertEqual(
            [name for name in FIELD_NAMES if name in fixed],
            [name for name in FIELD_NAMES if name in prompt],
        )
        self.assertEqual(h3_dialogue_blocks(fixed), h3_dialogue_blocks(prompt))

    def test_a_single_head_needs_no_merge(self):
        with self.assertRaises(PromptFixError):
            merge_subject_definitions(PROMPT)


class ParsingTests(unittest.TestCase):
    def test_the_decisions_are_read_from_the_block(self):
        fixes = parse_prompt_fixes(
            "\nSET_SPEAKER 3 S2\n2) SET_SUBJECT 2 Ricardo\nNAME_SUBJECT 1 Valeria\nMERGE_SUBJECTS\n"
        )

        self.assertEqual([fix["operation"] for fix in fixes], [
            "SET_SPEAKER", "SET_SUBJECT", "NAME_SUBJECT", "MERGE_SUBJECTS",
        ])
        self.assertEqual(fixes[0], {"operation": "SET_SPEAKER", "line": 3, "speaker": 2})
        self.assertEqual(fixes[1]["person"], "Ricardo")

    def test_no_fix_is_a_decision_not_an_operation(self):
        self.assertEqual(parse_prompt_fixes("NO_FIX"), [])

    def test_a_malformed_decision_is_refused_rather_than_dropped(self):
        with self.assertRaises(PromptFixError):
            parse_prompt_fixes("SET_SPEAKER two S2")

    def test_several_decisions_are_applied_in_order(self):
        prompt = PROMPT.replace(
            "<Subject 2> (S2): Ricardo, in a charcoal sweater.",
            "<Subject 2> (S2): facial, bodily, and character identity come from <Picture 2>.",
        )

        fixed, notes = apply_prompt_fixes(
            prompt,
            [
                {"operation": "NAME_SUBJECT", "subject": 2, "person": "Ricardo"},
                {"operation": "SET_SPEAKER", "line": 2, "speaker": 2},
            ],
            project_context=CONTEXT,
        )

        self.assertIn("(S2) <d>[Spanish] Baslyamente", fixed)
        self.assertIn("Ricardo, facial", fixed)
        self.assertEqual(len(notes), 2)


class ShotMarkerTests(unittest.TestCase):
    """A correction must not be thrown away over a marker Maestro writes itself.

    Measured on shot 109: the diagnosis was right -- the prose describes as feminine the person
    whose three lines belong to (S2) -- and the answer was rejected with "The rewrite lost its
    [Shot 1] marker", so the director got no fix at all.
    """

    ORIGINAL = (
        "detailed_description: The target video keeps its texture. [Shot 1] The scene continues "
        "in the loft. (S2) speaks thoughtfully: <d>[Spanish] hola.</d>.\n"
    )

    def test_the_marker_goes_back_where_the_original_had_it(self):
        rewritten = (
            "detailed_description: The target video keeps its texture. The scene continues "
            "in the loft. (S2) speaks thoughtfully: <d>[Spanish] hola.</d>.\n"
        )

        fixed = h3_restore_shot_marker(self.ORIGINAL, rewritten)

        self.assertIn("[Shot 1] The scene continues in the loft.", fixed)

    def test_a_rewrite_that_keeps_its_marker_is_untouched(self):
        self.assertEqual(h3_restore_shot_marker(self.ORIGINAL, self.ORIGINAL), self.ORIGINAL)

    def test_nothing_is_invented_when_the_original_has_no_marker(self):
        original = "detailed_description: (S2) speaks: <d>[Spanish] hola.</d>.\n"
        rewritten = "detailed_description: (S2) speaks: <d>[Spanish] hola.</d>.\n"

        self.assertEqual(h3_restore_shot_marker(original, rewritten), rewritten)

    def test_the_marker_lands_at_the_action_head_when_the_words_are_gone(self):
        rewritten = (
            "detailed_description: A man and a woman talk in a loft, warmly.\n"
            "overall_soundscape: Quiet room tone.\n"
        )

        fixed = h3_restore_shot_marker(self.ORIGINAL, rewritten)

        self.assertIn("detailed_description: [Shot 1] A man and a woman talk", fixed)


if __name__ == "__main__":
    unittest.main()
