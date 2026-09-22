"""Repair the film position recorded in a Director project's clip sidecars.

Regenerating a shot, or finishing a run that was resumed, used to leave
``director_clip_index`` out of step with the shot's real place in the film:

* a resumed run submits only the missing shots, so the batch numbered them from
  zero again and every shot of the tail was filed ``offset`` places early;
* a single-clip rerun published one output with no ``clip_output_files`` map, so
  the key was dropped entirely.

The gallery uses that key to hold a film together in shot order, and the clip
actions use it to decide which shot to regenerate — a wrong value regenerates the
wrong shot, which is why this needs repairing rather than ignoring.

Only ``director_clip_index`` is rewritten; every other sidecar key is preserved.
Run without ``--apply`` first: it reports what it would change and writes nothing.

    python scripts/repair_director_clip_index.py outputs/podcast-5
    python scripts/repair_director_clip_index.py outputs/podcast-5 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _pipeline_states(folder: str) -> list[str]:
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.name.startswith("_director_pipeline_") and entry.name.endswith(".json"):
                    found.append(entry.path)
    except OSError as exc:
        raise SystemExit(f"Cannot read {folder}: {exc}") from exc
    return sorted(found)


def _sidecar_path(folder: str, video_filename: str) -> str:
    stem, _ = os.path.splitext(video_filename)
    return os.path.join(folder, stem + ".meta.json")


def _mark_superseded_takes(folder: str, state: dict, apply: bool) -> int:
    """Point a regenerated take at the take it replaced.

    The gallery uses that marker to label the newer file and to stack it directly
    above the older one, so the user can compare them and delete the bad take.
    Only an unambiguous pair (exactly one other file for the same shot) is
    marked; a shot with several historical takes is left alone.
    """

    by_index: dict[int, list[str]] = {}
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith(".meta.json"):
                    continue
                try:
                    with open(entry.path, encoding="utf-8") as handle:
                        sidecar = json.load(handle)
                except (OSError, ValueError):
                    continue
                if not isinstance(sidecar, dict):
                    continue
                if sidecar.get("director_pipeline_id") != state.get("pipeline_id"):
                    continue
                index = sidecar.get("director_clip_index")
                stem = entry.name[: -len(".meta.json")]
                if isinstance(index, int) and os.path.isfile(os.path.join(folder, stem)):
                    by_index.setdefault(index, []).append(stem)
    except OSError:
        return 0

    marked = 0
    for clip in state.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        index, video = clip.get("index"), clip.get("video_filename")
        if not isinstance(index, int) or not video:
            continue
        others = [name for name in by_index.get(index, []) if name != video]
        if len(others) != 1:
            continue
        sidecar_path = _sidecar_path(folder, video)
        try:
            with open(sidecar_path, encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError):
            continue
        if sidecar.get("director_supersedes") == others[0]:
            continue
        print(f"clip {index}: new take {video[:44]} replaces {others[0][:44]}")
        if apply:
            sidecar["director_supersedes"] = others[0]
            temp_path = sidecar_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(sidecar, handle)
            os.replace(temp_path, sidecar_path)
        marked += 1
    return marked


def repair(folder: str, apply: bool) -> int:
    changed = 0
    missing_sidecar = 0
    for state_path in _pipeline_states(folder):
        try:
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"skip {os.path.basename(state_path)}: {exc}")
            continue
        pipeline_id = state.get("pipeline_id") or "?"
        for clip in state.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            index = clip.get("index")
            video = clip.get("video_filename")
            if not isinstance(index, int) or not video:
                continue
            sidecar_path = _sidecar_path(folder, video)
            if not os.path.isfile(sidecar_path):
                missing_sidecar += 1
                continue
            try:
                with open(sidecar_path, encoding="utf-8") as handle:
                    sidecar = json.load(handle)
            except (OSError, ValueError) as exc:
                print(f"skip {os.path.basename(sidecar_path)}: {exc}")
                continue
            if not isinstance(sidecar, dict):
                continue
            if sidecar.get("director_clip_index") == index:
                continue
            previous = sidecar.get("director_clip_index")
            print(
                f"{pipeline_id} clip {index}: director_clip_index "
                f"{previous!r} -> {index}  ({os.path.basename(video)[:52]})"
            )
            if apply:
                sidecar["director_clip_index"] = index
                sidecar.setdefault("director_pipeline_id", pipeline_id)
                temp_path = sidecar_path + ".tmp"
                with open(temp_path, "w", encoding="utf-8") as handle:
                    json.dump(sidecar, handle)
                os.replace(temp_path, sidecar_path)
            changed += 1
    verb = "rewrote" if apply else "would rewrite"
    print(f"\n{verb} {changed} sidecar(s); {missing_sidecar} clip(s) had no sidecar.")
    marked = 0
    for state_path in _pipeline_states(folder):
        try:
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        marked += _mark_superseded_takes(folder, state, apply)
    print(f"{verb} {marked} replaced-take marker(s).")
    if (changed or marked) and not apply:
        print("Re-run with --apply to write the changes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="Director output folder, e.g. outputs/podcast-5")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the changes. Without it the script only reports them.",
    )
    args = parser.parse_args()
    return repair(args.folder, args.apply)


if __name__ == "__main__":
    sys.exit(main())
