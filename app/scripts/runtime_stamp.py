"""Whether this installation was last built for the code it is running.

Pinokio's Update skips the dependency install, the kernel step and the UI build when
``git pull`` reports nothing new. That is true of the pull and says nothing about the
tree: a merge performed outside Pinokio -- an editor, an AI assistant, a hand-run
``git merge`` -- leaves the pull with nothing to say while the code it just brought in
needs all three steps. Measured on the operator's install after the 3.0.0 merge: Update
printed "Already up to date ... skipped the full dependency install and UI rebuild"
while ``requirements.txt`` had gained three pins and a new kernel installer.

So the decision is recorded rather than inferred: the build path writes the commit and
the dependency manifests it just satisfied, and the next Update compares against them.

Usage (from ``app``):

    python scripts/runtime_stamp.py --check   # prints stamp-current or stamp-stale: ...
    python scripts/runtime_stamp.py --write   # records the current code and manifests

``--check`` always exits 0 so a launcher can branch on the token instead of on a status.
The stamp is a dotfile in ``app`` and is ignored by ``app/.gitignore``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

STAMP_NAME = ".maestro-runtime-stamp"
MANIFESTS = (os.path.join("app", "requirements.txt"), os.path.join("ui", "package-lock.json"))


def _app_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_root(app_dir: str) -> str:
    return os.path.dirname(app_dir)


def _git_head(repo_root: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _digest(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError:
        return "missing"


def _current(app_dir: str, repo_root: str) -> dict[str, str]:
    return {
        "code": _git_head(repo_root) or "unknown",
        **{
            name.replace(os.sep, "/"): _digest(os.path.join(repo_root, name))
            for name in MANIFESTS
        },
    }


def _read_stamp(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            rows = handle.read().splitlines()
    except OSError:
        return {}
    return dict(
        row.split("=", 1) for row in rows if "=" in row
    )


def main(argv: list[str]) -> int:
    app_dir = _app_dir()
    repo_root = _repo_root(app_dir)
    stamp_path = os.path.join(app_dir, STAMP_NAME)
    current = _current(app_dir, repo_root)

    if "--write" in argv:
        with open(stamp_path, "w", encoding="utf-8", newline="\n") as handle:
            for key, value in current.items():
                handle.write(f"{key}={value}\n")
        print(f"stamp-written {current['code']}")
        return 0

    recorded = _read_stamp(stamp_path)
    if not recorded:
        # No record yet: the environment predates this check, so it has to be built
        # once to be trusted. Every later Update compares against the stamp.
        print("stamp-stale: no record of a completed build")
        return 0
    if current["code"] == "unknown":
        # Without a readable HEAD every build would record "unknown" and compare equal
        # to the next one, so an unreadable repository is never called current.
        print("stamp-stale: the repository head could not be read")
        return 0
    for key, value in current.items():
        if recorded.get(key) != value:
            # Say which one: a manifest change and a code change need different steps.
            print(
                f"stamp-stale: {key} recorded {recorded.get(key) or 'nothing'}, "
                f"now {value}"
            )
            return 0
    print("stamp-current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
