"""Audit every shot of a project against the project's own contract.

Reads a saved director state and reports, per shot, what the project's rules say and what
the shot does, without changing anything:

    python scripts/audit_project_prompts.py
    python scripts/audit_project_prompts.py --workspace magnifica-humanitas --verbose
    python scripts/audit_project_prompts.py --json logs/audit.json

The faults it looks for are the ones measured on a real 177-shot project: a shot built on
the wrong Subject (the voice lands on the other character), a duplicated
``subject_definitions`` head, a spoken line whose cue names nobody (the renderer refuses the
shot), the same line given to a different speaker than the neighbouring shot gives it, and
drafts where the action sections are missing or written in a different order than the rest
of the project. See ``app/services/director/prompt_audit.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services.director.prompt_audit import audit_project_prompts, format_audit_report  # noqa: E402

_OUTPUTS = os.path.join(_APP, "outputs")


def _newest_state(workspace: str) -> str:
    folder = os.path.join(_OUTPUTS, workspace)
    candidates = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.startswith("_director_pipeline_") and name.endswith(".json")
    ]
    if not candidates:
        raise SystemExit(f"no saved director state in {folder}")
    return max(candidates, key=os.path.getmtime)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="magnifica-humanitas")
    parser.add_argument("--state", default="", help="path to a saved director state")
    parser.add_argument("--json", default="", help="also write the audit as json here")
    parser.add_argument("--verbose", action="store_true", help="every finding, not a sample")
    args = parser.parse_args()

    state_path = args.state or _newest_state(args.workspace)
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
    clips = state.get("clips") or []

    audit = audit_project_prompts(clips)
    print(f"state: {os.path.basename(state_path)}")
    print(format_audit_report(audit, limit=len(clips) if args.verbose else 8))
    if args.verbose:
        print()
        for shot in audit["findings"]:
            print(f"shot {shot['shot']}:")
            for finding in shot["findings"]:
                print(f"    [{finding['severity']:7s}] {finding['code']}: {finding['message']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(audit, handle, ensure_ascii=False, indent=2)
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
