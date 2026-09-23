"""Give every clip of a project the general block it is missing.

Measured on `magnifica-humanitas` (177 clips): the project rules, the subject lock, the
wardrobe, the lighting arc and the technical specs are held ONCE in each clip's state
(4,421 characters) and are supposed to travel with every clip, because each clip is
generated on its own. Only 67 of the 177 promoted that text into the prompt that gets
rendered; 110 were rendering with no project rules at all, which is the drift between
shots they were meant to prevent.

The direction that actually moves a shot is a few hundred characters, so the general text
is not what an edit needs: it is frozen in the revision gate and assembled here.

Idempotent: a clip that already carries a base is left untouched, so running it twice
changes nothing the second time. Dry-run by default; `--apply` writes with a backup.

    python scripts/restore_project_base.py
    python scripts/restore_project_base.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_dialogue import (  # noqa: E402
    h3_dialogue_blocks,
    h3_ensure_project_base,
)


def _state_files(outputs_dir: str, project: str):
    root = os.path.join(outputs_dir, project) if project else outputs_dir
    folders = [root] + [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if os.path.isdir(os.path.join(root, name))
    ]
    for folder in folders:
        for name in sorted(os.listdir(folder)):
            if name.startswith("_director_pipeline_") and name.endswith(".json"):
                yield os.path.join(folder, name)


def repair(path: str, *, apply: bool) -> dict:
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    clips = state.get("clips") or []
    changed = []
    for clip in clips:
        prompt = str(clip.get("video_prompt") or "")
        context = str(clip.get("_director_project_context") or "")
        if not prompt or not context:
            continue
        updated, was_changed = h3_ensure_project_base(prompt, context)
        if not was_changed:
            continue
        if h3_dialogue_blocks(updated) != h3_dialogue_blocks(prompt):
            raise SystemExit(f"refusing: the spoken lines would change in {path}")
        changed.append((clip.get("index"), prompt, updated))
    if apply and changed:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.bak-{stamp}"
        shutil.copy2(path, backup)
        for clip in clips:
            prompt = str(clip.get("video_prompt") or "")
            context = str(clip.get("_director_project_context") or "")
            if not prompt or not context:
                continue
            updated, was_changed = h3_ensure_project_base(prompt, context)
            if was_changed:
                clip["video_prompt"] = updated
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.utime(path, (os.path.getmtime(backup), os.path.getmtime(backup)))
    return {
        "path": path,
        "clips": len(clips),
        "changed": len(changed),
        "added": sum(len(new) - len(old) for _, old, new in changed),
        "first": changed[0] if changed else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", default=os.path.join(_APP_DIR, "outputs"))
    parser.add_argument("--project", default="", help="only this project folder")
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    total_changed = 0
    for path in _state_files(args.outputs, args.project):
        report = repair(path, apply=args.apply)
        if not report["changed"]:
            continue
        total_changed += report["changed"]
        print(
            f"{os.path.relpath(report['path'], args.outputs)}: "
            f"{report['changed']} of {report['clips']} clips gain the project block "
            f"(+{report['added']} characters)"
        )
        first = report["first"]
        if first:
            index, old, new = first
            print(f"   clip {index}: {len(old)} -> {len(new)} characters")
    print(
        f"{'Applied' if args.apply else 'Dry run'}: {total_changed} clip(s) "
        + ("changed." if args.apply else "would change (use --apply).")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
