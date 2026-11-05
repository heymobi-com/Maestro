"""Exercise the clean-repository guard against isolated Git indexes."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUARD_SOURCE = REPOSITORY_ROOT / "scripts" / "verify_clean_repo.py"


class RepositoryPrivacyBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)

        (self.repo / "scripts").mkdir()
        (self.repo / "app").mkdir()
        shutil.copyfile(GUARD_SOURCE, self.repo / "scripts" / "verify_clean_repo.py")
        shutil.copyfile(REPOSITORY_ROOT / ".gitignore", self.repo / ".gitignore")
        shutil.copyfile(REPOSITORY_ROOT / "app" / ".gitignore", self.repo / "app" / ".gitignore")
        self._git("init", "--quiet")

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"git {' '.join(args)} failed:\n{result.stdout}{result.stderr}",
        )
        return result

    def _write(self, relative_path: str, content: str = "fixture\n") -> None:
        destination = self.repo / Path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _run_guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "verify_clean_repo.py")],
            cwd=self.repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_normal_git_add_keeps_ignored_notes_and_settings_untracked(self) -> None:
        ignored_paths = [
            ".codex-tmp/local-plan.md",
            "docs/development/working-plan.md",
            "ENVIRONMENT",
            "app/settings/user-settings.json",
        ]
        for path in ignored_paths:
            self._write(path, "local-only fixture\n")
            self.assertEqual(self._git("check-ignore", "--quiet", "--", path).returncode, 0)

        self._git("add", "--all")
        tracked = set(self._git("ls-files").stdout.splitlines())
        for path in ignored_paths:
            self.assertNotIn(path, tracked)

    def test_force_added_private_paths_are_rejected_without_scanning_content(self) -> None:
        private_paths = [
            ".codex-tmp/private-notes.md",
            ".codex/plan.md",
            ".claude/settings.local.json",
            ".agents/work-notes.md",
            ".aws/credentials",
            ".ssh/id_ed25519",
            "docs/development/private-plan.md",
            "docs/private/research.md",
            "AGENTS.md",
            "ENVIRONMENT",
            "docs/release_todo.md",
            ".env",
            ".env.staging",
            "app/.env",
            "uploads/private-upload.md",
            "logs/private-session.md",
            "app/env-sol/lib/private-state.md",
            "app/outputs/rendered-output.md",
            "app/uploads/imported-media.md",
            "app/settings/user-settings.md",
            "app/storage/local-state.md",
            "app/services_config.json",
            "app/wgp_config.json",
            "app/plugins_local.json",
            "app/loras_url_cache.json",
            "app/presets.json",
        ]
        sensitive_phrase = "fuck" + "ing"
        sentinel = "MAESTRO_PRIVATE_SENTINEL_6C712B"
        for path in private_paths:
            self._write(path, f"{sensitive_phrase} {sentinel}\n")

        # These representative note and settings paths are ignored by the real
        # project rules; force-add them to exercise the guard's index boundary.
        for path in (".codex-tmp/private-notes.md", "uploads/private-upload.md"):
            self.assertEqual(self._git("check-ignore", "--quiet", "--", path).returncode, 0)
        self._git("add", "--force", "--", *private_paths)

        result = self._run_guard()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("never-publish path(s)", result.stdout)
        for path in private_paths:
            self.assertIn(path, result.stdout)
        self.assertNotIn(sensitive_phrase, result.stdout)
        self.assertNotIn(sentinel, result.stdout)

    def test_public_docs_launchers_examples_and_vendored_source_are_allowed(self) -> None:
        public_paths = [
            "docs/technical/integration.md",
            "docs/testing/how-to-run.md",
            "tests/test_public_api.py",
            "start.js",
            ".env.example",
            ".env.sample",
            "app/.env.example",
            "app/.env.sample",
            "app/services_config.example.json",
            "app/wgp_config.schema.json",
            "app/plugins/example-vendor/README.md",
            "app/third_party/public-library/module.py",
            "app/third_party/public-library/presets.json",
            "tests/fixtures/services_config.json",
        ]
        for path in public_paths:
            self._write(path)
        self._git("add", "--force", "--", *public_paths)

        result = self._run_guard()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: tracked source is clean", result.stdout)

    def test_existing_generated_artifact_rules_still_reject_force_added_paths(self) -> None:
        generated_paths = [
            "app/services/_supplement_pack/provenance.md",
            "app/postprocessing/seedvc/LICENSE",
            "app/finetunes/local-model.json",
            "app/models/example.guide.md",
            "app/models/example.civitai.json",
        ]
        for path in generated_paths:
            self._write(path)
        self._git("add", "--force", "--", *generated_paths)

        result = self._run_guard()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("never-publish path(s)", result.stdout)
        for path in generated_paths:
            self.assertIn(path, result.stdout)


if __name__ == "__main__":
    unittest.main()
