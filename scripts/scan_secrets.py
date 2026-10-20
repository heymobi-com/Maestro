"""Refuse a credential-shaped string in the tracked tree.

The repository is public, so a token committed once is disclosed the moment it is
pushed -- rewriting the commit afterwards does not put it back. GitHub's own push
protection is the first line, but it has to be enabled per fork, and it only knows
the providers on its list. This is the local line: no dependencies, no network,
fast enough to run inside the unit-test battery so CI catches it on every push.

Usage:

    python scripts/scan_secrets.py              # tracked files, exit 1 on a finding
    python scripts/scan_secrets.py --staged     # what a commit is about to add
    python scripts/scan_secrets.py --history    # every blob ever committed (slow)

A finding prints file, line, the pattern name and a masked prefix -- never the value.

``ALLOWED_LITERALS`` holds the fixtures this repository deliberately contains: the
test-bench writes a fake key to a temp file to prove redaction. They are listed
verbatim so adding another one is a deliberate, reviewable edit rather than a
pattern that quietly widens.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# (name, compiled pattern, characters to keep when masking)
PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("private-key-block",
     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"), 0),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 4),
    ("github-token",
     re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})\b"), 6),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"), 5),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), 8),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), 4),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), 6),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 5),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"), 8),
    ("json-web-token",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), 8),
    # A URL that carries its own credentials: the shape a pasted connection string has.
    ("url-basic-auth", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@\"']+:[^/\s@\"']{6,}@"), 0),
)

# Values this repository contains on purpose, each with its reason.
# The url-basic-auth entry is the matched prefix of a localhost test fixture in
# tests/test_lmstudio_management.py, which proves a URL carrying credentials is
# rejected before any request is made. Neither the host nor the credential is real.
ALLOWED_LITERALS: dict[str, str] = {
    "sk-very-long-secret-value":
        "tests/test_testbench.py writes it to a temp file to prove the redactor removes it",
    "sk-secret":
        "tests/test_testbench.py asserts it never reaches a log line",
    "http://user:secret@":
        "tests/test_lmstudio_management.py rejects a credential-carrying localhost URL",
}

SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz",
    ".xz", ".7z", ".so", ".dll", ".exe", ".dylib", ".pt", ".pth", ".ckpt", ".bin",
    ".safetensors", ".gguf", ".onnx", ".mp4", ".webm", ".mov", ".wav", ".mp3",
    ".woff", ".woff2", ".ttf", ".otf", ".pyc",
)
MAX_BYTES = 4_000_000


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return completed.stdout


def _mask(value: str, keep: int) -> str:
    if keep <= 0 or len(value) <= keep:
        return "*" * max(1, min(len(value), 8))
    return value[:keep] + "*" * min(len(value) - keep, 10)


def _scan_text(text: str, where: str) -> list[str]:
    findings: list[str] = []
    for name, pattern, keep in PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if value in ALLOWED_LITERALS:
                continue
            line = text[:match.start()].count("\n") + 1
            findings.append(f"{where}:{line}: {name} {_mask(value, keep)}")
    return findings


def _tracked_files() -> list[str]:
    return [line for line in _git("ls-files").splitlines() if line.strip()]


def _staged_files() -> list[str]:
    return [line for line in _git("diff", "--cached", "--name-only", "--diff-filter=AM").splitlines()
            if line.strip()]


def scan_working_tree(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if path.lower().endswith(SKIP_SUFFIXES):
            continue
        try:
            if __import__("os").path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                findings.extend(_scan_text(handle.read(), path))
        except OSError:
            continue
    return findings


def scan_history() -> list[str]:
    """Every blob ever committed, deduplicated by object id."""
    findings: list[str] = []
    seen: set[str] = set()
    for row in _git("rev-list", "--all", "--objects").splitlines():
        parts = row.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, name = parts
        if sha in seen or name.lower().endswith(SKIP_SUFFIXES):
            continue
        seen.add(sha)
        body = _git("cat-file", "-p", sha)
        if body:
            findings.extend(_scan_text(body, f"history:{name}"))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staged", action="store_true",
                        help="scan only what the staged commit adds or changes")
    parser.add_argument("--history", action="store_true",
                        help="scan every blob ever committed (slow)")
    args = parser.parse_args(argv)

    if args.history:
        findings = scan_history()
        scope = "the whole history"
    elif args.staged:
        findings = scan_working_tree(_staged_files())
        scope = "the staged commit"
    else:
        findings = scan_working_tree(_tracked_files())
        scope = "the tracked tree"

    if findings:
        print(f"REFUSED: {len(findings)} credential-shaped string(s) in {scope}.")
        for finding in findings:
            print("  " + finding)
        print(
            "\nRemove the value, rotate it where it was issued, and if it was already\n"
            "pushed treat it as disclosed: amending the commit does not retract it."
        )
        return 1

    print(f"OK: no credential-shaped string in {scope}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
