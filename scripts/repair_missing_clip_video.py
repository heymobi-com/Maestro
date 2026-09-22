"""Re-point a clip slot whose recorded video file no longer exists.

A slot keeps the filename of the take it was last rendered from. If that file is
removed -- most easily from the gallery, by deleting a take that turned out to be
the one the film was using -- the slot points at nothing, and the rejoin refuses
the whole film:

    Regenerate missing or invalid video clip(s) 46 before rejoining.

Any surviving take of that shot can stand in, because every clip sidecar records
the slot it belongs to (`director_clip_index`). This finds the newest valid one
and re-points the slot at it, so the film can be joined without re-rendering.

Usage:
    python scripts/repair_missing_clip_video.py <outputs_folder> [--apply]

Dry-run by default: it only reports what it would rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.director_pipeline import (  # noqa: E402
    apply_slot_repoints,
    plan_slot_repoints,
)


def _state_files(folder: str) -> list[str]:
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.startswith("_director_pipeline_") and entry.name.endswith(".json"):
                    found.append(entry.path)
    except OSError:
        pass
    return sorted(found)


def repair(folder: str, apply: bool) -> int:
    verb = "rewrote" if apply else "would rewrite"
    total = 0
    for state_path in _state_files(folder):
        try:
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        plan = plan_slot_repoints(state, os.path.dirname(state_path))
        for fix in plan:
            print(
                f"  shot {fix['index'] + 1}: {(fix['previous'] or '<empty>')[:46]} "
                f"-> {fix['replacement'][:46]}"
            )
        if apply and plan:
            apply_slot_repoints(state, plan)
            temp_path = state_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(temp_path, state_path)
        total += len(plan)

    print(f"\n{verb} {total} clip slot(s).")
    if total and not apply:
        print("Re-run with --apply to write the changes.")
    if total == 0:
        print("Every slot points at a file that exists.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="outputs folder holding _director_pipeline_*.json")
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()
    return repair(args.folder, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
