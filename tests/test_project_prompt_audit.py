"""The project audit: every shot checked against the project's own contract.

The faults here were measured on a real 177-shot project, and the director's complaint was
that they only surfaced by hand, after a render. These tests pin the comparison and, just as
importantly, the two ways this measurement could lie: reading a binding line as if it were
the shot's field head, and counting differences as if they were shots.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from services.director.prompt_audit import (  # noqa: E402
    audit_project_prompts,
    format_audit_report,
    project_subject_lock,
)

PROJECT_CONTEXT = (
    "RESTRICCIONES GLOBALES DEL PROYECTO (critico, no negociable):\n"
    "- Este proyecto tiene EXACTAMENTE DOS participantes. No hay un tercero.\n"
    "SUBJECT LOCK (critico, no negociable):\n"
    "- <Subject 1> es SIEMPRE Valeria. <Subject 2> es SIEMPRE Ricardo.\n"
    "- Nunca se intercambian los Subjects entre shots.\n"
    "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
    "- <Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>.\n"
)


def shot(index: int, prompt: str, draft: str = "", context: str = PROJECT_CONTEXT) -> dict:
    entry = {
        "index": index,
        "video_prompt": prompt,
        "_director_project_context": context,
    }
    if draft:
        entry["_director_h3_source_prompt"] = draft
    return entry


LOCKED_HEAD = (
    "subject_definitions: <Subject 1> (S1): Valeria , leaning into the frame.\n"
    "<Subject 2> (S2): Ricardo , listening from the side.\n\n"
    "summary: [reference generation] Opening composition: Medium close-up.\n\n"
)
SWAPPED_HEAD = (
    "subject_definitions: <Subject 1> (S1): Ricardo , leaning into the frame.\n"
    "<Subject 2> (S2): Valeria , listening from the side.\n\n"
    "summary: [reference generation] Opening composition: Medium close-up.\n\n"
)
BINDINGS = (
    "- <Subject 1> (S1) = Valeria = [Speaker_01] = <Picture 1> + <Audio 1>.\n"
    "- <Subject 2> (S2) = Ricardo = [Speaker_02] = <Picture 2> + <Audio 2>.\n"
)


def codes(shot_entry: dict) -> list[str]:
    return [finding["code"] for finding in shot_entry["findings"]]


class SubjectLockTests(unittest.TestCase):
    def test_the_lock_is_read_from_the_project_text(self):
        self.assertEqual(project_subject_lock(PROJECT_CONTEXT), {1: "Valeria", 2: "Ricardo"})

    def test_a_project_without_a_lock_cannot_judge_shots(self):
        audit = audit_project_prompts([shot(0, SWAPPED_HEAD + BINDINGS, context="")])

        self.assertEqual(audit["subject_lock"], {})
        # Without a lock there is nothing to contradict, so no shot is accused of a swap: a
        # measurement that invents a fault is worse than no measurement.
        self.assertNotIn("subject-swap", codes(audit["findings"][0]) if audit["findings"] else [])


class SubjectSwapTests(unittest.TestCase):
    def test_a_shot_built_on_the_wrong_person_is_named(self):
        audit = audit_project_prompts([shot(0, SWAPPED_HEAD + BINDINGS)])

        # Both are true of this prompt: the head swaps the pair and the binding block
        # further down keeps the project's order, so the shot contradicts itself as well.
        self.assertIn("subject-swap", codes(audit["findings"][0]))
        self.assertIn("subject-head-vs-binding", codes(audit["findings"][0]))
        message = next(
            finding["message"]
            for finding in audit["findings"][0]["findings"]
            if finding["code"] == "subject-swap"
        )
        self.assertIn("<Subject 1> as Ricardo", message)
        self.assertIn("lock says Valeria", message)

    def test_a_shot_that_follows_the_lock_is_clean(self):
        audit = audit_project_prompts([shot(0, LOCKED_HEAD + BINDINGS)])

        self.assertEqual(audit["findings"], [])
        self.assertEqual(audit["totals"], {})

    def test_the_field_head_is_read_before_the_binding_lines(self):
        """The binding block agrees with the lock, so a later match must not win.

        Reading the last mention of each Subject made every swapped shot look clean, which
        is exactly the fault this audit exists to find.
        """

        audit = audit_project_prompts([shot(0, SWAPPED_HEAD + BINDINGS)])

        self.assertIn("subject-swap", codes(audit["findings"][0]))

    def test_one_swap_is_one_shot_not_two(self):
        """A shot that swaps both Subjects produces two differences and one shot."""

        audit = audit_project_prompts([shot(0, SWAPPED_HEAD + BINDINGS)])

        self.assertEqual(len(audit["findings"]), 1)
        self.assertEqual(audit["totals"]["subject-swap"], 1)

    def test_a_subject_the_project_never_declared_is_named(self):
        prompt = LOCKED_HEAD.replace(
            "<Subject 2> (S2): Ricardo , listening from the side.",
            "<Subject 2> (S2): Ricardo , listening from the side.\n<Subject 3> (S3): someone else.",
        )

        audit = audit_project_prompts([shot(0, prompt)])

        self.assertIn("subject-unlisted", codes(audit["findings"][0]))


class StructureTests(unittest.TestCase):
    def test_a_second_subject_definitions_head_is_named(self):
        prompt = (
            "subject_definitions: <Subject 1> is Valeria (S1): Valeria , in a navy blazer.\n"
            "subject_definitions: <Subject 2> is Ricardo (S2): Ricardo, older man.\n\n"
        )

        audit = audit_project_prompts([shot(0, prompt)])

        self.assertIn("subject-field-duplicated", codes(audit["findings"][0]))

    def test_a_draft_without_an_action_section_is_a_warning_not_an_error(self):
        audit = audit_project_prompts(
            [shot(0, LOCKED_HEAD + BINDINGS, draft="[Shot 1] The scene continues in the loft.")]
        )

        finding = audit["findings"][0]["findings"][0]
        self.assertEqual(finding["code"], "action-section-missing")
        self.assertEqual(finding["severity"], "warning")
        self.assertIn("editing", finding["message"])

    def test_a_draft_carrying_the_project_text_is_named(self):
        audit = audit_project_prompts(
            [shot(0, LOCKED_HEAD + BINDINGS, draft="[Shot 1] " + "contexto " * 300)]
        )

        self.assertIn("project-text-in-draft", codes(audit["findings"][0]))

    def test_the_orders_the_project_uses_are_counted(self):
        clippy = [
            shot(0, LOCKED_HEAD, draft="Opening composition: a\nDialogue: b\nCanonical identity and world: c"),
            shot(1, LOCKED_HEAD, draft="Opening composition: a\nDialogue: b\nCanonical identity and world: c"),
            shot(2, LOCKED_HEAD, draft="Opening composition: a\nDialogue: b"),
        ]

        audit = audit_project_prompts(clippy)

        self.assertEqual(
            audit["action_order"],
            ["Opening composition", "Dialogue:", "Canonical identity and world"],
        )
        self.assertEqual(sum(audit["action_order_variants"].values()), 3)
        self.assertIn("action-order-differs", codes(audit["findings"][0]))


class LinesBetweenShotsTests(unittest.TestCase):
    def test_the_same_line_on_a_different_speaker_in_the_next_shot_is_named(self):
        line = "<d>[Spanish] Sonaba muy frio.</d>"
        first = LOCKED_HEAD + f"detailed_description: (S1) says: {line}.\n" + BINDINGS
        second = LOCKED_HEAD + f"detailed_description: (S2) says: {line}.\n" + BINDINGS
        clips = [shot(0, first), shot(1, second)]

        audit = audit_project_prompts(clips)

        self.assertIn("line-speaker-crosses-shots", codes(audit["findings"][0]))

    def test_a_line_the_renderer_cannot_assign_is_an_error(self):
        prompt = LOCKED_HEAD + "detailed_description: His voice says: <d>[Spanish] hola.</d>.\n"

        audit = audit_project_prompts([shot(0, prompt)])

        finding = audit["findings"][0]["findings"][0]
        self.assertEqual(finding["code"], "line-without-speaker")
        self.assertEqual(finding["severity"], "error")


class ReportTests(unittest.TestCase):
    def test_the_report_counts_shots_and_says_nothing_changed(self):
        audit = audit_project_prompts([shot(0, SWAPPED_HEAD + BINDINGS)])

        report = format_audit_report(audit)

        self.assertIn("1 shot(s) checked", report)
        self.assertIn("subject-swap", report)
        self.assertIn("Nothing was changed", report)

    def test_a_clean_project_says_so(self):
        report = format_audit_report(audit_project_prompts([shot(0, LOCKED_HEAD + BINDINGS)]))

        self.assertIn("no inconsistencies found", report)


if __name__ == "__main__":
    unittest.main()
