"""Repair clips whose saved prompt declares the film's shot, not the clip's.

The shot-breakdown guide asks the planner for ``[Shot 1]`` at the start and then
"later cuts begin [Shot N] At MM:SS.mmm", and the planner answered with the
film's own shot number. Clip 10 of a real project was saved as:

    [Shot 1] Opening composition: Close up shot focusing on emotional depth..
    Canonical identity and world: ... [Shot 11] At MM:37.500, the camera pushes
    into a tight close-up of Valeria (S1).

The compiler prepends its own clip-local ``[Shot 1]`` on top of whatever the body
already carried, so that body declared two shots. 162 of that project's 177 clips
carried both, and 16 also carried a film-absolute timestamp beyond their own
length. A model told that a later shot follows performs that framing inside the
same clip, which is how a character placed in the later one is rendered twice.

The compiler normalizes new prompts now. This rewrites the ones already saved:
the first marker stays, later ones go, and the prose that followed them -- the
clip's own body -- is untouched. The raw planner prompt is normalized too, so a
future recompile cannot bring the number back.

Usage:
    python scripts/repair_multi_shot_clip_prompts.py <outputs_folder> [...] [--apply]

Dry-run by default: it prints what it would rewrite and writes nothing. Add
``--apply`` to save, which copies each state file to ``.bak-<timestamp>`` first.
Prompts the user edited by hand are reported and left alone unless
``--include-edited`` is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.director.h3_dialogue import (  # noqa: E402
    _declared_shot_numbers,
    _single_shot_body,
)

# Both names H3 uses for the body field. Only the body is normalized: the
# retention rows legitimately carry "[Shot 1]" references, and stripping those
# would break the format.
_BODY_FIELDS = ("detailed_description", "integrated_multimodal_description")


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


def _normalize_body_field(prompt: str) -> str:
    """Drop the extra markers inside the body field, and nothing else."""

    text = str(prompt or "")
    for field in _BODY_FIELDS:
        pattern = re.compile(
            rf"(?mis)^(\s*{field}\s*:)(.*?)(?=^\s*\w[\w_]*\s*:|\Z)"
        )
        match = pattern.search(text)
        if not match:
            continue
        body = match.group(2)
        fixed = _single_shot_body(body)
        if fixed != body:
            text = text[: match.start(2)] + fixed + text[match.end(2) :]
    return text


def _normalize_clip(clip: dict) -> tuple[str, str]:
    """The repaired compiled prompt and raw prompt for one clip."""

    compiled = _normalize_body_field(str(clip.get("video_prompt") or ""))
    source = _single_shot_body(str(clip.get("_director_h3_source_prompt") or ""))
    return compiled, source


def _reads_as_multi_shot(text: str) -> bool:
    return any(number > 1 for number in _declared_shot_numbers(text))


def _repair_state(path: str, apply: bool, include_edited: bool) -> dict:
    state = json.load(open(path, encoding="utf-8"))
    clips = state.get("clips") or []
    report = {
        "path": path,
        "clips": len(clips),
        "flagged": [],
        "edited_skipped": [],
        "examples": [],
        "before": 0,
        "after": 0,
    }
    backups = 0
    for clip in clips:
        compiled = str(clip.get("video_prompt") or "")
        source = str(clip.get("_director_h3_source_prompt") or "")
        if not (_reads_as_multi_shot(compiled) or _reads_as_multi_shot(source)):
            continue
        numbers = _declared_shot_numbers(compiled)
        report["flagged"].append(clip.get("index"))
        report["before"] += 1 if _reads_as_multi_shot(compiled) else 0
        if clip.get("_director_prompt_user_edited") and not include_edited:
            report["edited_skipped"].append(clip.get("index"))
            continue
        fixed_compiled, fixed_source = _normalize_clip(clip)
        if fixed_compiled == compiled and fixed_source == source:
            continue
        report["after"] += 1
        if len(report["examples"]) < 3:
            report["examples"].append(
                {
                    "index": clip.get("index"),
                    "numbers": numbers,
                    "fixed": _declared_shot_numbers(fixed_compiled),
                    "chars": f"{len(compiled)} -> {len(fixed_compiled)}",
                }
            )
        if apply:
            clip["video_prompt"] = fixed_compiled
            if source:
                clip["_director_h3_source_prompt"] = fixed_source
            # The compile cache belongs to the text that just changed.
            clip["_director_h3_compiled_prompt"] = ""
    if apply and report["after"]:
        backup = path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        backups = 1
    report["backups"] = backups
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folders", nargs="+", help="project output folders")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default: report only)",
    )
    parser.add_argument(
        "--include-edited",
        action="store_true",
        help="also rewrite prompts the user edited by hand",
    )
    args = parser.parse_args(argv)

    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"=== {mode} ===")
    totals = {"states": 0, "clips": 0, "before": 0, "after": 0, "edited": 0}
    for folder in args.folders:
        for path in _state_files(folder):
            report = _repair_state(path, args.apply, args.include_edited)
            if not report["flagged"]:
                continue
            totals["states"] += 1
            totals["clips"] += report["clips"]
            totals["before"] += report["before"]
            totals["after"] += report["after"]
            totals["edited"] += len(report["edited_skipped"])
            print()
            print(f"  {os.path.basename(path)}")
            print(f"      clips                     : {report['clips']}")
            print(f"      declaraban varios shots   : {report['before']}")
            print(f"      se reescribirian          : {report['after']}")
            if report["edited_skipped"]:
                print(
                    "      editados a mano, sin tocar: "
                    f"{len(report['edited_skipped'])} {report['edited_skipped'][:8]}"
                )
            for example in report["examples"]:
                print(
                    f"      clip {example['index']}: {example['numbers']} "
                    f"-> {example['fixed']}  (caracteres {example['chars']})"
                )
            if report["backups"]:
                print("      copia de seguridad        : si (.bak-<fecha>)")
    print()
    print("=== resumen ===")
    print(f"  estados de proyecto con hallazgos: {totals['states']}")
    print(f"  clips revisados                  : {totals['clips']}")
    print(f"  clips con marcador global        : {totals['before']}")
    print(f"  clips que se reescriben          : {totals['after']}")
    print(f"  omitidos por edicion manual      : {totals['edited']}")
    if not args.apply:
        print()
        print("  Nada se ha escrito. Repite con --apply para aplicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
