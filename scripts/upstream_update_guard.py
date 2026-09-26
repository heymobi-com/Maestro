#!/usr/bin/env python3
"""Seam guard: keep our code out of files upstream owns, and predict merges.

Maestro upstream releases land every few weeks, and our fork develops between
them. Every line of ours that lives inside a file upstream also owns is a line
that can conflict, so that surface is measured, ratcheted and reviewed here
instead of being discovered in the middle of a merge.

Three things are checked:

- ``measure()`` counts our added lines per *shared* file: ``git diff
  origin/main HEAD --numstat`` restricted to the paths that are really in
  upstream. Lines we add in files only we have are free and are not counted.
- ``check()`` compares that against ``upstream_seam_budget.json``. The budget is
  a ratchet: extracting code lowers it, and nothing may raise it by accident.
  A file that was not in the budget at all means we just started editing another
  upstream file, which has to be a deliberate decision.
- ``predict()`` runs ``git merge-tree`` so the conflicts of the next update are
  known *before* the working tree is touched, and flags any predicted conflict
  that is not a known seam.

Usage
-----
    python scripts/upstream_update_guard.py              # check the ratchet
    python scripts/upstream_update_guard.py --predict    # what would conflict
    python scripts/upstream_update_guard.py --write-baseline

A measured merge is also a validated one: the numbers here come from the same
two refs the merge uses, so an update that passes this check has a known
conflict set rather than a hoped-for one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUDGET_PATH = Path(__file__).resolve().parent / "upstream_seam_budget.json"

UPSTREAM = "origin/main"
HEAD = "HEAD"

# Merge-tree prints these as progress and advice; they are not conflict lines.
_MERGE_TREE_NOISE = (
    "Auto-merging",
    "Auto-merged",
    "CONFLICT",
    "Merging",
    "warning:",
    "error:",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _exists_in(rev: str, path: str) -> bool:
    return _git("cat-file", "-e", f"{rev}:{path}").returncode == 0


def measure(baseline: str = UPSTREAM, head: str | None = None) -> dict[str, int]:
    """Our added lines per file that the baseline also has.

    ``head`` defaults to the working tree, so the number reflects what is on disk
    right now rather than the last commit: a seam that grows has to be seen
    before it is committed, not after.

    Binary entries come back as ``-`` and are skipped: they cannot conflict as
    text, and a count of them would be meaningless anyway.
    """
    out: dict[str, int] = {}
    args = ["diff", baseline] if head is None else ["diff", baseline, head, "--numstat"]
    if head is None:
        args.append("--numstat")
    result = _git(*args)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")

    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, _deleted, path = parts[0], parts[1], parts[2]
        if added == "-":
            continue
        if not _exists_in(baseline, path):
            continue
        out[path] = int(added)
    return out


def compare(actual: dict[str, int], budget: dict[str, int]) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Return (regressions, newcomers).

    A regression is a shared file that carries more of our lines than its
    budget. A newcomer is a shared file with no budget line at all: it became a
    seam without anyone deciding to open one.
    """
    regressions = [
        (path, budget[path], actual.get(path, 0))
        for path in sorted(budget)
        if actual.get(path, 0) > budget[path]
    ]
    newcomers = sorted(set(actual) - set(budget))
    return regressions, newcomers


def parse_conflicts(merge_tree_output: str) -> list[str]:
    """The conflicted paths out of ``git merge-tree --write-tree --name-only``.

    The first line is the resulting tree object; the conflicted paths follow it,
    then a blank line and the usual git chatter.
    """
    conflicts: list[str] = []
    for index, raw in enumerate(merge_tree_output.splitlines()):
        line = raw.strip()
        if index == 0:
            continue
        if not line:
            break
        if line.startswith(_MERGE_TREE_NOISE):
            break
        conflicts.append(line)
    return conflicts


def load_budget() -> dict:
    if not BUDGET_PATH.exists():
        raise SystemExit(
            f"{BUDGET_PATH.name} is missing. Create it deliberately with --write-baseline."
        )
    return json.loads(BUDGET_PATH.read_text(encoding="utf-8"))


def write_baseline(notes: dict[str, str] | None = None) -> None:
    payload = load_budget() if BUDGET_PATH.exists() else {}
    payload["files"] = dict(sorted(measure().items()))
    if notes is not None:
        payload["notes"] = {k: v for k, v in notes.items() if k in payload["files"]}
    payload.setdefault("upstream", UPSTREAM)
    BUDGET_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {BUDGET_PATH.relative_to(REPO)} with {len(payload['files'])} shared files")


def check() -> int:
    budget = load_budget()
    limits = budget["files"]
    actual = measure()
    regressions, newcomers = compare(actual, limits)

    total_actual = sum(actual.values())
    total_budget = sum(limits.values())
    print(f"seam: {len(actual)} shared files, {total_actual} lines of ours inside upstream")
    print(f"budget: {len(limits)} files, {total_budget} lines")
    print()

    worst = sorted(actual.items(), key=lambda item: item[1], reverse=True)[:12]
    for path, lines in worst:
        limit = limits.get(path)
        mark = "  " if limit is None else ("!!" if lines > limit else "ok")
        shown = "no budget" if limit is None else f"budget {limit}"
        print(f"{mark} {lines:>6}  {shown:<12}  {path}")

    if not regressions and not newcomers:
        print()
        print("OK: no shared file carries more of our code than it is allowed to.")
        return 0

    if regressions:
        print()
        print("REGRESSION -- extract the code, do not raise the budget:")
        for path, limit, lines in regressions:
            print(f"  {path}: {lines} lines, budget {limit} (+{lines - limit})")

    if newcomers:
        print()
        print("NEW SHARED FILE -- we started editing a file upstream owns:")
        for path in newcomers:
            print(f"  {path}: {actual[path]} lines, no budget line")

    return 1


def predict() -> int:
    """Predict the conflicts of merging upstream, without touching the tree."""
    result = _git("merge-tree", "--write-tree", "--name-only", HEAD, UPSTREAM)
    if result.returncode == 0:
        print(f"merging {UPSTREAM} into {HEAD}: no conflicts predicted")
        return 0

    conflicts = parse_conflicts(result.stdout)
    limits = load_budget()["files"]
    print(f"merging {UPSTREAM} into {HEAD}: {len(conflicts)} conflict(s) predicted")
    unexpected = []
    for path in conflicts:
        if path in limits:
            print(f"  known seam   {path} (budget {limits[path]} lines)")
        else:
            print(f"  UNEXPECTED   {path} -- not in the seam budget")
            unexpected.append(path)

    if unexpected:
        print()
        print("A conflict in a file we believed was safe: inspect it before merging.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--predict", action="store_true", help="predict the next merge's conflicts")
    parser.add_argument("--write-baseline", action="store_true", help="record the current surface")
    parser.add_argument("--print-list", action="store_true", help="print every shared file")
    args = parser.parse_args(argv)

    if args.write_baseline:
        write_baseline()
        return 0
    if args.predict:
        return predict()
    if args.print_list:
        actual = measure()
        for path, lines in sorted(actual.items(), key=lambda item: -item[1]):
            print(f"{lines:>6}  {path}")
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())
