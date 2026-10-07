#!/usr/bin/env python3
"""Report how a Director plan distributes a staged dramatic effect across its shots.

A process with stages -- a plant sprouting, blooming and drying; a storm arriving and
clearing -- is one story beat spread over several consecutive shots. When a plan writes the
whole process into a single shot and then repeats it, the effects flash past and lose their
intention, which is the reported "no hilacion de escena entre un clip y otro".

This reads a saved plan (``app/outputs/*/_director_pipeline_*.json``) and reports, effect by
effect, how many shots carry it and how long each of those shots is. It reads
``_director_h3_source_prompt``, the planner's own immutable result, so it shows the plan
rather than anything the compiler did to it.

Usage
-----
    python scripts/director_effect_spread.py                     # the newest plan
    python scripts/director_effect_spread.py Hasta-el-final      # a named workspace
    python scripts/director_effect_spread.py --list              # list saved plans
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(REPO, "app", "outputs")
_PIPELINE_PREFIX = "_director_pipeline_"

# A staged effect the plan may either spread across shots or repeat whole. Both languages:
# a plan takes the language of the brief it was written from.
EFFECTS = {
    "plants": (
        r"\bplants?\b|\bcreepers?\b|\bvines?\b|\bbranches?\b|\bleaves\b|"
        r"\bbloom(?:s|ing)?\b|\bflourish\w*\b|\bwither\w*\b|\bdry(?:ing)? up\b|"
        r"\bplantas?\b|\bflorec\w+\b|\bmarchit\w+\b"
    ),
    "weather": (
        r"\brain\b|\brains\b|\brainfall\b|\braindrops?\b|\blluvia\b|"
        r"storm|lightning|thunder"
    ),
}


def _states() -> list[str]:
    return sorted(
        glob.glob(os.path.join(OUTPUTS, "*", f"{_PIPELINE_PREFIX}*.json")),
        key=os.path.getmtime,
        reverse=True,
    )


def _report_effects(clips: list[dict[str, Any]]) -> None:
    for name, pattern in EFFECTS.items():
        regex = re.compile(pattern, re.IGNORECASE)
        hits = []
        for index, clip in enumerate(clips):
            source = clip.get("_director_h3_source_prompt") or ""
            terms = sorted({match.group(0).lower() for match in regex.finditer(source)})
            if terms:
                hits.append((index, len(terms), terms, clip.get("_director_duration_sec")))
        print(f"=== effect: {name} -> in {len(hits)} of {len(clips)} shots ===")
        for index, count, terms, duration in hits:
            print(f"  shot {index:>3} (dur {duration}) {count} terms: {', '.join(terms)}")
        whole = [h for h in hits if h[1] >= 5]
        if len(whole) > 1:
            print(
                f"  !! {len(whole)} shots carry most of the process at once "
                f"(shots {', '.join(str(h[0]) for h in whole)}): it is repeating, not advancing"
            )
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", nargs="?", help="workspace name; newest plan if omitted")
    parser.add_argument("--list", action="store_true", help="list saved plans and stop")
    args = parser.parse_args(argv)

    states = _states()
    if not states:
        print("no saved plans under app/outputs")
        return 1
    if args.list:
        for path in states[:20]:
            stamp = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
            print(f"  {stamp}  {os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}")
        return 0

    path = states[0]
    if args.workspace:
        matching = [
            candidate for candidate in states
            if os.path.basename(os.path.dirname(candidate)) == args.workspace
        ]
        if not matching:
            print(f"no saved plan for workspace {args.workspace!r}; try --list")
            return 1
        path = matching[0]

    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    clips = state.get("clips") or []
    print(f"plan     : {os.path.relpath(path, REPO)}")
    print(f"workspace: {state.get('workspace')} | status: {state.get('status')}")
    print(f"shots    : {len(clips)}")
    durations = [
        clip.get("_director_duration_sec") for clip in clips
        if isinstance(clip.get("_director_duration_sec"), (int, float))
    ]
    if durations:
        print(
            f"duration : min {min(durations):.1f}s | max {max(durations):.1f}s | "
            f"mean {sum(durations) / len(durations):.1f}s"
        )
    print()
    _report_effects(clips)
    return 0


if __name__ == "__main__":
    sys.exit(main())
