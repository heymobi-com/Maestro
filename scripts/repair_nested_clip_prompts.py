"""Repair clips whose saved prompt is a *compiled* one, which nests on rerun.

Saving a reviewed prompt used to store whatever the Dashboard showed -- a
compiled six-field Context-IR prompt -- in the field that is supposed to hold
the planner's raw shot prompt. Every rerun then compiled a compiled prompt, so
the dialogue and the DIALOGUE FORMAT block were duplicated per save. One real
shot reached 17,331 characters with its lines present twice, and MiniMax H3
could no longer tell which referenced character spoke:

    MiniMax H3 Omni could not determine which referenced character speaks
    '[Spanish] Pensemos, por ejemplo, en la evolucion de las redes sociales...'

This restores the raw planner prompt from the run's own snapshot, recompiles it
once, and clears the edit flag, so the shot is back to a normal state.

Usage:
    python scripts/repair_nested_clip_prompts.py <outputs_folder> [--apply]

Dry-run by default: it only reports what it would rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.director.h3_dialogue import (  # noqa: E402
    compile_h3_clip_plans,
    looks_like_compiled_h3_prompt,
)
from services.director_pipeline import _director_h3_reference_manifest  # noqa: E402

_PLAN_FIELDS = (
    "_director_subjects_on_screen",
    "_director_duration_sec",
    "_director_project_context",
    "_director_speaker_registry",
    "_director_audio_plan",
    "_director_opening_blocking",
    "_director_closing_blocking",
    "_director_h3_prompt_mode",
    "_director_h3_model_family",
    "_director_vocal_contract",
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


def _build_plan(clip: dict, raw_source: str, planned: dict) -> dict:
    plan = {
        "video_prompt": raw_source,
        "image_prompt": clip.get("image_prompt", ""),
        "_director_h3_source_prompt": raw_source,
        "_director_h3_compiled_prompt": "",
        "_director_dialogue_beats": planned.get("_director_dialogue_beats") or [],
        "_director_prompt_user_edited": False,
    }
    for field in _PLAN_FIELDS:
        if clip.get(field) is not None:
            plan[field] = clip[field]
        elif planned.get(field) is not None:
            plan[field] = planned[field]
    return plan


def _references_for(state: dict, clip_out_dir: str) -> list:
    """The same identity manifest the real rerun compiles against.

    Without it the rebuilt prompt loses every ``<Picture N>`` binding, and with
    it the subject mentions that let MiniMax H3 resolve which referenced
    character speaks a line. Rebuilding without it produced a prompt that would
    have failed exactly like the nested one it replaced.
    """

    snapshot = dict(state.get("_params_snapshot") or {})
    snapshot.setdefault(
        "generated_reference_image_filename",
        state.get("generated_reference_image_filename"),
    )
    try:
        return list(_director_h3_reference_manifest(
            snapshot, None, out_dir=clip_out_dir, drive_audio_path=None,
        ) or [])
    except Exception as exc:
        print(f"  reference manifest unavailable ({exc}); rebuilding without it")
        return []


def repair(folder: str, apply: bool) -> int:
    verb = "rewrote" if apply else "would rewrite"
    total = 0
    for state_path in _state_files(folder):
        try:
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        clip_out_dir = os.path.dirname(state_path)
        references = _references_for(state, clip_out_dir)
        clips = state.get("clips") or []
        planned_clips = (state.get("_params_snapshot") or {}).get("planned_clips") or []
        changed = False
        for index, clip in enumerate(clips):
            stored = str(clip.get("_director_h3_source_prompt") or "")
            if not looks_like_compiled_h3_prompt(stored):
                continue
            if index >= len(planned_clips) or not isinstance(planned_clips[index], dict):
                print(f"  clip {index}: no snapshot entry, left alone")
                continue
            raw = str(planned_clips[index].get("_director_h3_source_prompt") or "")
            if not raw:
                print(f"  clip {index}: snapshot has no raw prompt, left alone")
                continue
            plan = _build_plan(clip, raw, planned_clips[index])
            duration = clip.get("_director_duration_sec") or 0.0
            mode = str(clip.get("_director_h3_prompt_mode") or "").strip().lower() or None
            try:
                compile_h3_clip_plans(
                    [plan],
                    prompt_modes=[mode] if mode else None,
                    durations=[duration],
                    reference_manifests=[references] if references else None,
                )
            except Exception as exc:  # a clean rebuild is the whole point
                print(f"  clip {index}: rebuild failed ({exc}); left alone")
                continue
            print(
                f"  clip {index}: {len(stored)} -> {len(str(plan['video_prompt']))} chars "
                f"(raw source {len(raw)}, references {len(references)})"
            )
            if apply:
                clip["_director_h3_source_prompt"] = raw
                clip["_director_dialogue_beats"] = planned_clips[index].get("_director_dialogue_beats") or []
                clip["_director_h3_compiled_prompt"] = str(plan.get("_director_h3_compiled_prompt") or "")
                clip["video_prompt"] = str(plan["video_prompt"])
                clip["_director_prompt_user_edited"] = False
                for field in _PLAN_FIELDS:
                    if plan.get(field) is not None:
                        clip[field] = plan[field]
                changed = True
            total += 1
        if apply and changed:
            temp_path = state_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(temp_path, state_path)

    print(f"\n{verb} {total} clip prompt(s).")
    if total and not apply:
        print("Re-run with --apply to write the changes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="outputs folder holding _director_pipeline_*.json")
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()
    return repair(args.folder, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
