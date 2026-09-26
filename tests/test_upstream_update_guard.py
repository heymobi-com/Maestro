"""The seam guard has to notice our surface growing, and predict merges.

Upstream releases land every few weeks and our fork develops between them, so
the amount of our code living inside files upstream owns is a number that has to
stay visible. ``scripts/upstream_update_guard.py`` measures it, and these tests
pin the two decisions it encodes: an over-budget file or a file that became a
seam without a budget line is a failure, and a predicted merge conflict is only
acceptable inside a known seam.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import upstream_update_guard as guard  # noqa: E402  (needs the path insert above)


class CompareTests(unittest.TestCase):
    """The ratchet itself, on fixtures, so it cannot be satisfied by accident."""

    def test_a_file_under_its_budget_passes(self):
        regressions, newcomers = guard.compare({"a.py": 3}, {"a.py": 10})
        self.assertEqual([], regressions)
        self.assertEqual([], newcomers)

    def test_a_file_over_its_budget_is_a_regression(self):
        regressions, newcomers = guard.compare({"a.py": 11}, {"a.py": 10})
        self.assertEqual([("a.py", 10, 11)], regressions)
        self.assertEqual([], newcomers)

    def test_a_file_without_a_budget_line_is_reported(self):
        regressions, newcomers = guard.compare({"a.py": 3, "new.py": 4}, {"a.py": 10})
        self.assertEqual([], regressions)
        self.assertEqual(["new.py"], newcomers)

    def test_a_file_that_left_the_upstream_tree_is_not_a_regression(self):
        # It is no longer shared, so its budget line is simply stale.
        regressions, newcomers = guard.compare({"a.py": 1}, {"a.py": 10, "gone.py": 5})
        self.assertEqual([], regressions)
        self.assertEqual([], newcomers)

    def test_extraction_lowers_the_budget_without_complaint(self):
        regressions, newcomers = guard.compare({"a.py": 0}, {"a.py": 10})
        self.assertEqual([], regressions)
        self.assertEqual([], newcomers)


class ParseConflictsTests(unittest.TestCase):
    """``git merge-tree --write-tree --name-only`` output, as git really prints it."""

    OUTPUT = """8f4c1f0d1c0f4b2c8a7e5d3b1a9f0e2d4c6b8a10
ui/src/api/client.ts
ui/src/stores/useStore.ts

Auto-merging app/launch.py
CONFLICT (content): Merge conflict in ui/src/api/client.ts
"""

    def test_the_tree_object_line_is_not_a_path(self):
        conflicts = guard.parse_conflicts(self.OUTPUT)
        self.assertNotIn("8f4c1f0d1c0f4b2c8a7e5d3b1a9f0e2d4c6b8a10", conflicts)

    def test_the_conflicted_paths_are_returned_in_order(self):
        self.assertEqual(
            ["ui/src/api/client.ts", "ui/src/stores/useStore.ts"],
            guard.parse_conflicts(self.OUTPUT),
        )

    def test_the_chatter_after_the_blank_line_is_ignored(self):
        # "Auto-merging app/launch.py" is progress, not a conflict: a clean file
        # must never be reported as needing attention.
        self.assertNotIn("app/launch.py", guard.parse_conflicts(self.OUTPUT))

    def test_no_output_means_no_conflicts(self):
        self.assertEqual([], guard.parse_conflicts(""))


class RepoTests(unittest.TestCase):
    """The real numbers, so the budget cannot drift away from the tree."""

    def test_the_measurement_finds_our_code_inside_upstream_files(self):
        actual = guard.measure()
        # The two files where the last two merges conflicted: if the measurement
        # stops seeing them it is measuring the wrong refs.
        self.assertIn("ui/src/stores/useStore.ts", actual)
        self.assertIn("ui/src/api/client.ts", actual)
        self.assertGreater(actual["ui/src/stores/useStore.ts"], 0)

    def test_our_own_files_are_not_counted(self):
        actual = guard.measure()
        # A file only we have cannot conflict, so it must stay out of the seam.
        self.assertNotIn("ui/src/lib/directorScript.ts", actual)
        self.assertNotIn("app/services/director/voice_gender.py", actual)

    def test_the_repository_is_within_its_budget(self):
        self.assertEqual(0, guard.check())

    def test_every_budget_file_carries_a_reason(self):
        payload = json.loads(guard.BUDGET_PATH.read_text(encoding="utf-8"))
        # Every file we deliberately leave alone is explained, so the reason is
        # reviewable instead of remembered.
        for path, why in payload["notes"].items():
            self.assertIn(path, payload["files"])
            self.assertGreater(len(why), 20, path)

    def test_deferred_giants_say_why_they_are_deferred(self):
        payload = json.loads(guard.BUDGET_PATH.read_text(encoding="utf-8"))
        notes = payload["notes"]
        self.assertIn("app/services/director/h3_dialogue.py", notes)
        self.assertIn("churn", notes["app/services/director/h3_dialogue.py"])


if __name__ == "__main__":
    unittest.main()
