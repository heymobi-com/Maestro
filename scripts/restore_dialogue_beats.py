"""Restore the dialogue beats a saved prompt edit dropped from a clip.

Saving reviewed prompt text cleared ``_director_dialogue_beats`` outright, and a
rerun of an edited clip cleared it again. Those beats are the plan: who speaks
which line, in what order, with which delivery. Without them the H3 compiler
cannot rebuild a shot's dialogue and timing contract, and because a reviewed
compiled prompt is rendered verbatim nothing downstream restored it.

Measured on clip 13 of ``magnifica-humanitas``: 4 beats and a vocal contract
before the director note was applied, 0 beats and ``None`` after -- while the
prompt text was still asking for lip-sync.

The shot's plan still carries them (``planned_clip._director_dialogue_beats``).
This restores them, keeping only the lines the saved prompt still carries, so a
line the editor deleted cannot come back, and taking each speaker from the
prompt text the director actually approved.

Usage:
    python scripts/restore_dialogue_beats.py <outputs_folder> [...] [--apply]

Dry-run by default: it prints what it would restore and writes nothing. Add
``--apply`` to save, which copies each state file to ``.bak-<timestamp>`` first
and restores the original modification time, because other tests discover their
project fixtures by file date.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.director.h3_dialogue import (  # noqa: E402
    h3_dialogue_blocks,
    retain_dialogue_beats,
)


def _state_files(folder: str) -> list[str]:
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if (
                    entry.is_file()
                    and entry.name.startswith("_director_pipeline_")
                    and entry.name.endswith(".json")
                ):
                    found.append(entry.path)
    except OSError:
        pass
    return sorted(found)


def _planned_beats(clip: dict) -> list:
    """The beats the shot was planned with, wherever the plan stored them."""

    planned = clip.get("planned_clip") or {}
    beats = planned.get("_director_dialogue_beats") or []
    if beats:
        return list(beats)
    for window in clip.get("window_prompts_plan") or []:
        beats = (window or {}).get("_director_dialogue_beats") or []
        if beats:
            return list(beats)
    return []


def _clip_report(clip: dict) -> dict | None:
    """What this clip needs, or ``None`` when nothing is missing."""

    prompt = str(clip.get("video_prompt") or "")
    blocks = h3_dialogue_blocks(prompt)
    if not blocks:
        return None
    if clip.get("_director_dialogue_beats"):
        return None
    planned = _planned_beats(clip)
    restored = retain_dialogue_beats(planned, prompt)
    return {
        "index": clip.get("index"),
        "edited": bool(clip.get("_director_prompt_user_edited")),
        "blocks": len(blocks),
        "planned": len(planned),
        "restored": restored,
        "speakers": [beat.get("speaker_id", "") for beat in restored],
    }


def _repair_state(path: str, apply: bool) -> dict:
    state = json.load(open(path, encoding="utf-8"))
    clips = state.get("clips") or []
    repair = []
    unrecoverable = []
    for clip in clips:
        found = _clip_report(clip)
        if not found:
            continue
        if not found["restored"]:
            # The plan has nothing left to restore: report, never guess.
            unrecoverable.append(found["index"])
            continue
        repair.append(found)
    report = {
        "path": path,
        "clips": len(clips),
        "repair": repair,
        "unrecoverable": unrecoverable,
        "backups": 0,
    }
    if apply and repair:
        for found in repair:
            clip = clips[found["index"]]
            # Only the beats: the prompt text the director approved is untouched,
            # so the compile cache still belongs to it.
            clip["_director_dialogue_beats"] = found["restored"]
        original_mtime = os.path.getmtime(path)
        shutil.copy2(path, path + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.utime(path, (original_mtime, original_mtime))
        report["backups"] = 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folders", nargs="+", help="project output folders")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default: report only)",
    )
    args = parser.parse_args(argv)

    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"=== {mode} ===")
    totals = {"states": 0, "clips": 0, "restored": 0, "unrecoverable": 0}
    for folder in args.folders:
        for path in _state_files(folder):
            report = _repair_state(path, args.apply)
            if not report["repair"] and not report["unrecoverable"]:
                continue
            totals["states"] += 1
            totals["clips"] += report["clips"]
            totals["restored"] += len(report["repair"])
            totals["unrecoverable"] += len(report["unrecoverable"])
            print()
            print(f"  {os.path.basename(path)}")
            print(f"      clips                        : {report['clips']}")
            print(f"      sin beats pero con lineas    : {len(report['repair'])}")
            for found in report["repair"][:12]:
                edited = "editado" if found["edited"] else "original"
                print(
                    f"      clip {found['index']:<4} ({edited}): "
                    f"{found['blocks']} linea(s), plan {found['planned']} "
                    f"-> restauradas {len(found['restored'])} "
                    f"{found['speakers']}"
                )
            if report["unrecoverable"]:
                print(
                    "      sin plan recuperable         : "
                    f"{report['unrecoverable'][:12]}"
                )
            if report["backups"]:
                print("      copia de seguridad           : si (.bak-<fecha>)")
    print()
    print("=== resumen ===")
    print(f"  estados de proyecto con hallazgos : {totals['states']}")
    print(f"  clips sin beats                   : {totals['restored']}")
    print(f"  sin plan recuperable              : {totals['unrecoverable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
