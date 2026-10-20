"""The tracked tree carries no credential, and the scanner proves it can say no.

This runs inside the normal battery, so CI checks every push without a workflow
edit: the fork is public, and a token committed once is disclosed the moment it
is pushed. Reported from use -- a credential-hygiene review after a run of
account-takeover attempts -- so the check is a test rather than a habit.

``test_it_catches_*`` are the important half: a scanner that only ever says "OK"
is indistinguishable from one that is broken.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import scan_secrets  # noqa: E402


class TheTrackedTreeTests(unittest.TestCase):
    def test_no_credential_shaped_string_in_the_tracked_tree(self):
        findings = scan_secrets.scan_working_tree(scan_secrets._tracked_files())

        self.assertEqual(findings, [], "credential-shaped strings:\n  " + "\n  ".join(findings))


class TheScannerCanSayNoTests(unittest.TestCase):
    def _findings(self, text: str) -> list[str]:
        # Assemble the samples at runtime so this file never contains one itself.
        return scan_secrets._scan_text(text, "sample")

    def test_it_catches_a_private_key_block(self):
        dash = "-" * 5
        self.assertTrue(self._findings(f"{dash}BEGIN OPENSSH PRIVATE KEY{dash}"))

    def test_it_catches_a_github_token(self):
        token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        self.assertIn("github-token", " ".join(self._findings(f"token = '{token}'")))

    def test_it_catches_a_huggingface_token(self):
        token = "hf_" + "a" * 34
        self.assertIn("huggingface-token", " ".join(self._findings(f"HF_TOKEN={token}")))

    def test_it_catches_a_credential_in_a_url(self):
        self.assertIn("url-basic-auth", " ".join(self._findings("postgres://admin:hunter22@db/x")))

    def test_it_never_prints_the_value_it_caught(self):
        secret = "hf_" + "z" * 34
        rendered = " ".join(self._findings(f"HF_TOKEN={secret}"))

        self.assertNotIn(secret, rendered)
        self.assertIn("*", rendered)

    def test_the_documented_fixtures_stay_allowed(self):
        for literal in scan_secrets.ALLOWED_LITERALS:
            self.assertEqual(self._findings(f'"{literal}"'), [], literal)

    def test_every_allowed_literal_says_why(self):
        for literal, reason in scan_secrets.ALLOWED_LITERALS.items():
            self.assertTrue(reason.strip(), literal)


if __name__ == "__main__":
    unittest.main()
