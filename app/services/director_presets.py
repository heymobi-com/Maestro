"""A Director setup you can start from again.

Reported from use: *"cuando algo falla no puedo regresar a corregir sino que tengo que
volver a meter todo: personajes, referencias, volver a correr ... en este proceso de
ensayo y error esto se debe hacer muchas veces"*.

A Studio recipe is a LOOK preset -- model, tuning, LoRA pointers -- and
``recipes.RECIPE_PARAM_KEYS`` deliberately excludes the per-generation inputs (reference
images, start/end frames, audio guides). That is right for Studio and useless for
Director, where the cast images, the soundtrack and its transcript ARE the setup: a
Director project retyped by hand costs an upload session before the first LLM call.

A Director preset is the project's own snapshot frozen into a durable folder: the brief,
the ordered reference manifest with its files, the audio and its transcript analysis (so a
re-plan does not re-transcribe), the models, the LoRAs and every Director control. Applying
one fills the composer exactly the way Open & Edit fills it from a run.

What a preset is NOT is a plan: prompts, the clip timeline and the run internals are
stripped on save, so applying a preset can never re-render the shot that just failed.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from typing import Any, Mapping, Optional

__all__ = [
    "PRESET_CONTAINER",
    "delete_preset",
    "get_preset",
    "list_presets",
    "save_preset",
]
# Inside the workspace, beside ``_director_assets``: same serving rules, so a preset's
# images and audio load through the file endpoint that Open & Edit already uses.
PRESET_CONTAINER = "_director_presets"
PRESET_SCHEMA_VERSION = 1

# Run-scoped state. A preset carries what the DIRECTOR decided, never what a run
# produced: keeping these would restore the previous timeline, the previous prompts and
# the previous planning progress into a fresh project.
_RUN_SCOPED_PARAM_KEYS = frozenset({
    "planned_clips",
    "prepared_clip_plans",
    "prepared_planned_clips",
    "prepared_llm_log",
    "_director_planning_checkpoint",
    "_director_project_id",
    "_director_video_execution_profile",
    "_director_asset_manifest",
    "_director_shot_image_policy",
    "generated_reference_image_filename",
    "project_id",
    "queue_entry_id",
    "parent_pipeline_id",
    "pipeline_id",
})

# Inside ``director_ui_snapshot``: the same rule, at the field level.
_RUN_SCOPED_UI_KEYS = frozenset({
    "directorClipPlans",
    "directorPlannedClips",
    "directorLlmLog",
})

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _preset_root(out_dir: str) -> str:
    return os.path.join(out_dir, PRESET_CONTAINER)


def _preset_json_path(out_dir: str, preset_id: str) -> str:
    return os.path.join(_preset_root(out_dir), f"{preset_id}.json")


def _new_preset_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    slug = slug[:40].strip("-") or "director-setup"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def _card(payload: Mapping[str, Any]) -> dict:
    params = payload.get("params") or {}
    ui = params.get("director_ui_snapshot") or {}
    references = params.get("minimax_h3_references") or []
    return {
        "id": payload.get("id", ""),
        "name": payload.get("name", ""),
        "created_at": payload.get("created_at", 0),
        "source_pipeline_id": payload.get("source_pipeline_id", ""),
        "pipeline_type": str(params.get("pipeline_type") or ""),
        "video_model": str(params.get("video_model") or ""),
        "reference_count": len(references) if isinstance(references, list) else 0,
        "audio_name": str(ui.get("directorAudioName") or ""),
        "brief_chars": len(str(params.get("scene_description") or "")),
        "has_transcript": bool(ui.get("directorAnalysis")),
    }


def _strip_run_scoped(params: dict) -> None:
    for key in _RUN_SCOPED_PARAM_KEYS:
        params.pop(key, None)
    ui = params.get("director_ui_snapshot")
    if isinstance(ui, dict):
        for key in _RUN_SCOPED_UI_KEYS:
            ui.pop(key, None)


def _unreadable_assets(params: Mapping[str, Any]) -> list[str]:
    """Input files a preset would have to copy but cannot find.

    Only paths that were submitted are checked: a browser-side upload with no server
    path yet is not a broken setup, it is a setup that has nothing on disk to copy.
    """

    missing: list[str] = []
    for key in ("audio_path", "audio_vocals_path", "reference_image_path", "voice_reference"):
        value = str(params.get(key) or "").strip()
        if value and not os.path.isfile(value):
            missing.append(f"{key} ({os.path.basename(value)})")
    for key in ("character_ref_paths", "location_ref_paths"):
        for value in params.get(key) or []:
            path = str(value or "").strip()
            if path and not os.path.isfile(path):
                missing.append(f"{key} ({os.path.basename(path)})")
    for reference in params.get("minimax_h3_references") or []:
        if not isinstance(reference, Mapping):
            continue
        for key in ("path", "audio_path"):
            path = str(reference.get(key) or "").strip()
            if path and not os.path.isfile(path):
                role = str(reference.get("role") or reference.get("type") or "reference")
                missing.append(f"{role} ({os.path.basename(path)})")
    return missing


def save_preset(
    state: Mapping[str, Any],
    name: str,
    out_dir: str,
    *,
    source_pipeline_id: str = "",
) -> dict:
    """Freeze a saved pipeline's setup into a reusable Director preset.

    The assets are copied into the preset's own folder and the snapshot's paths are
    rewritten to point at those copies, so the preset survives deleting the run it came
    from and re-planning copies them again into the new project.
    """

    from services.director_pipeline import _materialize_director_assets

    raw = state.get("_params_snapshot")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(
            "This run has no saved setup to freeze. Runs created before the "
            "setup snapshot existed cannot become a preset; open the run and "
            "submit it once, then save the preset."
        )
    label = str(name or "").strip()
    if not label:
        raise ValueError("A preset name is required.")
    if not any(
        [
            str(raw.get("scene_description") or "").strip(),
            str(raw.get("audio_path") or "").strip(),
            raw.get("minimax_h3_references"),
            raw.get("reference_image_path"),
            raw.get("character_ref_paths"),
            raw.get("location_ref_paths"),
        ]
    ):
        raise ValueError(
            "There is nothing to save yet: add the audio, the references or the "
            "brief first, then save the setup."
        )

    # A setup whose media cannot be copied is worse than no setup: it restores with
    # missing images and the failure shows up later, inside a render.
    missing = _unreadable_assets(raw)
    if missing:
        raise ValueError(
            "These input files are no longer on disk, so the setup cannot own its own "
            "copies: " + "; ".join(missing[:6])
        )

    params = copy.deepcopy(raw)
    _strip_run_scoped(params)

    preset_id = _new_preset_id(label)
    os.makedirs(_preset_root(out_dir), exist_ok=True)
    manifest = _materialize_director_assets(
        params, out_dir, preset_id, container=PRESET_CONTAINER,
    )

    payload = {
        "preset_version": PRESET_SCHEMA_VERSION,
        "id": preset_id,
        "name": label,
        "created_at": time.time(),
        "source_pipeline_id": str(source_pipeline_id or ""),
        "params": params,
        "asset_manifest": manifest,
    }
    path = _preset_json_path(out_dir, preset_id)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    return _card(payload)


def list_presets(out_dir: str) -> list[dict]:
    root = _preset_root(out_dir)
    if not os.path.isdir(root):
        return []
    cards = []
    for filename in os.listdir(root):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(root, filename), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("id"):
            cards.append(_card(payload))
    return sorted(cards, key=lambda card: card.get("created_at", 0), reverse=True)


def get_preset(preset_id: str, out_dir: str) -> Optional[dict]:
    if not _SAFE_ID_RE.match(str(preset_id or "")):
        return None
    path = _preset_json_path(out_dir, preset_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def delete_preset(preset_id: str, out_dir: str) -> bool:
    if not _SAFE_ID_RE.match(str(preset_id or "")):
        return False
    path = _preset_json_path(out_dir, preset_id)
    if not os.path.isfile(path):
        return False
    import shutil

    try:
        os.remove(path)
    except OSError:
        return False
    shutil.rmtree(os.path.join(_preset_root(out_dir), preset_id), ignore_errors=True)
    return True
