"""Normalize a saved project's shot prompts: one cast block, numbered as the project locks it.

Repairs the two faults whose correct answer the project already writes down (see
``app/services/director/prompt_normalize.py``): a duplicated ``subject_definitions`` head and a
participant numbered as the other one. It never decides who speaks a line.

    python scripts/normalize_project_prompts.py                    # dry run, shows the diff
    python scripts/normalize_project_prompts.py --apply            # writes, with a backup
    python scripts/normalize_project_prompts.py --workspace X --apply

Every rewrite is checked before it is written: the spoken lines must be byte-identical, only the
cast block may change, one head must remain, and the shot must stop reporting the fault.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.director.h3_dialogue import h3_dialogue_blocks, h3_subject_binding_problems  # noqa: E402
from services.director.prompt_audit import audit_project_prompts  # noqa: E402
from services.director.prompt_normalize import (  # noqa: E402
    _SUBJECT_FIELD_RE,
    normalize_clip_prompts,
    subject_label_skeleton,
)

_OUTPUTS = os.path.join(_APP, "outputs")
# The duplicate head is left to the director: folding two cast blocks into one is a
# reformatting, and this script only rewrites numbers.
_FIXED_CODES = ("subject-swap",)


def _newest_state(workspace: str) -> str:
    folder = os.path.join(_OUTPUTS, workspace)
    states = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.startswith("_director_pipeline_") and name.endswith(".json")
    ]
    if not states:
        raise SystemExit(f"no saved director state in {folder}")
    return max(states, key=os.path.getmtime)


def _codes_for(clip: dict) -> set[str]:
    audit = audit_project_prompts([clip])
    if not audit["findings"]:
        return set()
    return {finding["code"] for finding in audit["findings"][0]["findings"]}


def _verify_field(field: str, before: str, after: str) -> str:
    """Why this rewrite must not be written, or an empty string when it is safe."""

    if h3_dialogue_blocks(before) != h3_dialogue_blocks(after):
        return f"{field}: the spoken lines would change"
    if len(_SUBJECT_FIELD_RE.findall(after)) != len(_SUBJECT_FIELD_RE.findall(before)):
        return f"{field}: the number of subject_definitions heads would change"
    if subject_label_skeleton(before) != subject_label_skeleton(after):
        # The strong one: with every Subject number blanked out, the two texts must be
        # identical, so nothing but a number may have moved.
        return f"{field}: something other than a Subject number would change"
    if h3_subject_binding_problems(after):
        return f"{field}: the head would still contradict the shot's own bindings"
    return ""


def _verify_clip(clip: dict, changed: dict[str, str]) -> str:
    """The reasons a clip must not be written, checked on the whole result.

    A shot can carry the swap in both its saved prompt and its draft, and checking one field
    while the other is still swapped refuses a repair that is correct once both are applied.
    """

    for field, after in changed.items():
        reason = _verify_field(field, str(clip.get(field) or ""), after)
        if reason:
            return reason
    combined = dict(clip)
    combined.update(changed)
    remaining = _codes_for(combined) & set(_FIXED_CODES)
    if remaining:
        return f"the shot would still report {sorted(remaining)}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="magnifica-humanitas")
    parser.add_argument("--state", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", type=int, default=1, help="diffs to print in the dry run")
    args = parser.parse_args()

    state_path = args.state or _newest_state(args.workspace)
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)

    before_audit = audit_project_prompts(state.get("clips") or [])
    planned: list[tuple[dict, dict[str, str]]] = []
    refused: list[tuple[int, str]] = []
    for clip in state.get("clips") or []:
        changed = normalize_clip_prompts(clip)
        if not changed:
            continue
        shot = int(clip.get("index", 0)) + 1
        reason = _verify_clip(clip, changed)
        if reason:
            refused.append((shot, reason))
        else:
            planned.append((clip, changed))

    print(f"state: {os.path.basename(state_path)}")
    print(f"shots: {before_audit['shots']}")
    print(f"faults before: {before_audit['totals']}")
    print(f"shots to repair: {len(planned)}")
    for shot, reason in refused:
        print(f"  REFUSED shot {shot}: {reason}")

    if not planned:
        print("nothing to write")
        return 0

    for index, (clip, changed) in enumerate(planned):
        if index >= args.show:
            break
        for field, after in changed.items():
            current = str(clip.get(field) or "")
            print(f"\n--- shot {int(clip.get('index', 0)) + 1} field {field} ---")
            for line in difflib.unified_diff(
                current[:1200].splitlines(), after[:1200].splitlines(),
                lineterm="", n=1,
            ):
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                    print("   " + line.strip()[:150])

    if not args.apply:
        print("\ndry run: nothing written. Pass --apply to write it.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{state_path}.bak-{stamp}"
    shutil.copy2(state_path, backup)
    for clip, changed in planned:
        clip.update(changed)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    after_audit = audit_project_prompts(state.get("clips") or [])
    print()
    print(f"backup: {os.path.basename(backup)}")
    print(f"faults after: {after_audit['totals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
