"""Update may not skip the install steps because git had nothing to pull.

Reported from use, after the 3.0.0 merge: Update printed "Already up to date - Web Push
runtime verified; skipped the full dependency install and UI rebuild" while
``requirements.txt`` had gained three pins and a new kernel installer. Nothing broke only
because the environment had been verified by hand, so the decision is recorded instead of
inferred from the pull text.

``scripts/runtime_stamp.py`` is the whole decision; this pins it.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from scripts import runtime_stamp  # noqa: E402

HEAD = "f2ea1b2e0e8d7fc23ee613fbf42ab1760c7ece02"
OTHER = "0000000000000000000000000000000000000000"


class StampDecisionTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.app_dir = self._dir.name
        # The module reads the layout from its own __file__ and git from the repository.
        self._patches = [
            mock.patch.object(runtime_stamp, "_app_dir", return_value=self.app_dir),
            mock.patch.object(runtime_stamp, "_git_head", return_value=HEAD),
            mock.patch.object(runtime_stamp, "_digest", return_value="aaaa"),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = runtime_stamp.main(list(argv))
        self.assertEqual(code, 0, "--check must never fail a launcher")
        return out.getvalue().strip()

    def test_an_installation_without_a_record_is_stale(self):
        # The environment predates the check, so it is built once to be trusted.
        self.assertIn("stamp-stale", self._run("--check"))
        self.assertIn("no record", self._run("--check"))

    def test_a_written_stamp_reads_back_current(self):
        self.assertIn("stamp-written", self._run("--write"))

        self.assertEqual(self._run("--check"), "stamp-current")

    def test_a_newer_commit_is_stale_and_says_which_value_moved(self):
        self._run("--write")
        with mock.patch.object(runtime_stamp, "_git_head", return_value=OTHER):
            decision = self._run("--check")

        self.assertIn("stamp-stale: code recorded " + HEAD, decision)
        self.assertIn(OTHER, decision)

    def test_a_changed_requirements_file_is_stale_although_the_commit_is_the_same(self):
        """The reported case: an in-place manifest edit shows up in no pull at all."""

        self._run("--write")
        with mock.patch.object(runtime_stamp, "_digest", return_value="bbbb"):
            decision = self._run("--check")

        self.assertIn("stamp-stale", decision)
        self.assertIn("app/requirements.txt", decision)

    def test_an_unreadable_repository_is_never_current(self):
        # With no HEAD every build would record "unknown" and match the next one.
        self._run("--write")
        with mock.patch.object(runtime_stamp, "_git_head", return_value=""):
            self.assertIn("stamp-stale", self._run("--check"))
            self.assertIn("head could not be read", self._run("--check"))

    def test_the_stamp_lives_where_git_already_ignores_it(self):
        # app/.gitignore starts with ".*", so a dotfile here needs no ignore change.
        self.assertTrue(runtime_stamp.STAMP_NAME.startswith("."))


if __name__ == "__main__":
    unittest.main()
