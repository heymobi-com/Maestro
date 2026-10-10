"""Imported checkpoints must not outlive their weights.

Reported from use: "baje por error el modelo equivocado lo borre pero sigue marcando que lo tengo
y no puedo bajar el correcto porque piensa que ahi lo tengo". Measured on that install, an
imported checkpoint is four artifacts and not one -- the weights in ``ckpts/``, a
``<file>.civitai.json`` provenance sidecar beside them, and one registration per workflow in
``app/finetunes/`` (``..._frames.json`` and ``..._references.json`` for a hybrid checkpoint, both
pointing at the same weights through a bare filename, so there is no URL left to re-fetch them
from).

Only the weights were deleted, so the rest stayed behind, and every view built from those
registrations kept reporting the checkpoint as installed while the model list kept offering a
model whose only copy could no longer be read or downloaded. Nothing in the interface could undo
it: the imported-checkpoints view has no uninstall and no rescan, and ``/api/v1/models/reload``
rebuilt the registry by updating entries in place, so a definition that vanished from disk stayed
in memory until the process restarted.

This module owns the missing quarter of that lifecycle. It reports a registration whose weights
are gone instead of implying they are there, forgets definitions the registry no longer has a file
for, clears a remembered selection that pointed at a removed model, and removes an imported
checkpoint as the single unit it was installed as.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Callable, Iterable, Mapping, MutableMapping

from fastapi import APIRouter, HTTPException

__all__ = [
    "mark_missing",
    "forget_missing_definitions",
    "clear_removed_models",
    "remove_imported_checkpoint",
    "router",
]

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FINETUNES_DIR = os.path.join(_APP_DIR, "finetunes")

# Preferences that name a model type as their key, and those that name it as their value.
_MODEL_KEY_MAPS = ("inference_steps_per_model", "director_max_shot_frames_per_model")
_MODEL_VALUE_MAPS = ("selected_model_per_mode", "selected_model_per_audio_sub_mode")

router = APIRouter()


def _checkpoint_dir() -> str:
    """The `ckpts/` root WGP loads weights from, where the sidecars are written."""
    import shared.utils.files_locator as fl

    root = fl.get_download_location()
    return root if os.path.isabs(root) else os.path.join(_APP_DIR, root)


def _locate_weight(filename: str):
    if not filename:
        return None
    import shared.utils.files_locator as fl

    try:
        return fl.locate_file(filename, error_if_none=False)
    except Exception:
        return None


def _is_protected(path: str) -> bool:
    import shared.utils.files_locator as fl

    try:
        return bool(fl.is_protected_path(path))
    except Exception:
        return False


def mark_missing(
    entries: Iterable[Mapping[str, Any]], *, locate: Callable[[str], Any] | None = None
) -> list[dict]:
    """Copy the imported-checkpoint entries, saying which ones lost their weights.

    The list is built from registrations, which say what was imported rather than what is on disk,
    so without this an install whose weights are gone reads exactly like a working one.
    """
    find = locate or _locate_weight
    marked = []
    for entry in entries:
        row = dict(entry)
        row["missing"] = find(str(entry.get("filename") or "")) is None
        marked.append(row)
    return marked


def forget_missing_definitions(models_def: MutableMapping[str, Any], app_dir: str = "") -> list[str]:
    """Drop registry entries whose definition file is gone, and name them.

    ``load_model_definitions`` updates entries in place for every file it finds, so a definition
    that disappeared from disk survives every reload: this is the prune that pass never did.
    """
    root = app_dir or _APP_DIR
    present = {
        os.path.basename(path)[: -len(".json")]
        for pattern in ("defaults/*.json", "finetunes/*.json")
        for path in glob.glob(os.path.join(root, pattern))
    }
    removed = [model_type for model_type in sorted(models_def) if model_type not in present]
    for model_type in removed:
        models_def.pop(model_type, None)
    return removed


def clear_removed_models(preferences: Mapping[str, Any], removed: Iterable[str]) -> tuple[dict, list[str]]:
    """Return the preferences without the removed model types, and which maps changed.

    Two shapes to clear, because the remembered Studio state uses both: a per-model table keyed by
    model type (remembered steps, Director clip limits) and a per-mode table whose values are model
    types (the selected model of each mode). Clearing only the first is how a removed checkpoint
    stays the Studio's own choice.
    """
    gone = set(removed)
    updated = dict(preferences or {})
    cleared = []
    for key in _MODEL_KEY_MAPS:
        table = updated.get(key)
        if not isinstance(table, dict):
            continue
        kept = {name: value for name, value in table.items() if name not in gone}
        if len(kept) != len(table):
            updated[key] = kept
            cleared.append(key)
    for key in _MODEL_VALUE_MAPS:
        table = updated.get(key)
        if not isinstance(table, dict):
            continue
        kept = {mode: model for mode, model in table.items() if model not in gone}
        if len(kept) != len(table):
            updated[key] = kept
            cleared.append(key)
    return updated, cleared


def _registrations(finetunes_dir: str):
    """Every registration that records a CivitAI import, as (path, model block, civitai block)."""
    for path in sorted(glob.glob(os.path.join(finetunes_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                definition = json.load(handle)
        except (OSError, ValueError):
            continue
        model = definition.get("model") if isinstance(definition, dict) else None
        civitai = model.get("civitai") if isinstance(model, dict) else None
        if isinstance(civitai, dict) and civitai.get("modelId"):
            yield path, model, civitai


def _identity(civitai: Mapping[str, Any]) -> tuple:
    """What makes two registrations the same install: one file, one weight file name."""
    return civitai.get("fileId") or 0, str(civitai.get("filename") or "").casefold()


def _remove(path: str, record: list[str]) -> None:
    try:
        os.remove(path)
        record.append(os.path.basename(path))
    except OSError:
        pass


def remove_imported_checkpoint(
    model_type: str,
    *,
    finetunes_dir: str = "",
    checkpoint_dir: str = "",
    locate: Callable[[str], Any] | None = None,
    protected: Callable[[str], bool] | None = None,
) -> dict:
    """Remove an imported checkpoint as one unit: registrations, weights and sidecar.

    Every registration that shares the removed one's CivitAI identity goes with it, because the
    workflows of one import share a single weight file and a half-removed import is what leaves a
    model list advertising weights that are already gone. Only the file the registration records
    as its own is deleted, never a shared companion such as the VAE or the audio stack, and never a
    weight that lives in a linked read-only folder.
    """
    finetunes = finetunes_dir or _FINETUNES_DIR
    registrations = list(_registrations(finetunes))
    target = next(
        (entry for entry in registrations if os.path.basename(entry[0])[: -len(".json")] == model_type),
        None,
    )
    if target is None:
        raise KeyError(model_type)

    identity = _identity(target[2])
    family = [entry for entry in registrations if _identity(entry[2]) == identity]
    removed: list[str] = []
    removed_registrations = []
    for path, _model, _civitai in family:
        removed_registrations.append(os.path.basename(path)[: -len(".json")])
        _remove(path, removed)

    find = locate or _locate_weight
    guard = protected or _is_protected
    root = checkpoint_dir or _checkpoint_dir()
    deleted_weights, kept_weights, deleted_sidecars = [], [], []
    for _path, _model, civitai in family:
        filename = str(civitai.get("filename") or "")
        if not filename:
            continue
        resolved = find(filename)
        if resolved:
            if guard(resolved):
                kept_weights.append(filename)
            else:
                _remove(resolved, deleted_weights)
        sidecar = os.path.join(root, os.path.splitext(os.path.basename(filename))[0] + ".civitai.json")
        if os.path.isfile(sidecar):
            _remove(sidecar, deleted_sidecars)

    return {
        "model_type": model_type,
        "registrations": sorted(removed_registrations),
        "removed_registrations": removed,
        "deleted_weights": deleted_weights,
        "kept_weights": sorted(kept_weights),
        "deleted_sidecars": deleted_sidecars,
    }


def _persist_server_config() -> None:
    """Write wgp.server_config the way the preference endpoints do, atomically."""
    import wgp

    config_path = os.path.abspath(wgp.server_config_filename)
    temp_path = f"{config_path}.{os.getpid()}.{id(object())}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(wgp.server_config, handle, indent=4)
        os.replace(temp_path, config_path)
    finally:
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _forget_everywhere(removed: Iterable[str]) -> list[str]:
    """Take the removed model types out of the live registry and the remembered selections."""
    import wgp

    names = sorted(set(removed))
    for model_type in names:
        wgp.models_def.pop(model_type, None)
        displayed = getattr(wgp, "displayed_model_types", None)
        if isinstance(displayed, list) and model_type in displayed:
            displayed.remove(model_type)
    preferences = wgp.server_config.get("maestro_studio_preferences") or {}
    updated, cleared = clear_removed_models(preferences, names)
    if cleared:
        wgp.server_config["maestro_studio_preferences"] = updated
        _persist_server_config()
    return cleared


@router.delete("/api/v1/checkpoints/{model_type}")
def remove_imported_checkpoint_endpoint(model_type: str):
    """Remove an imported checkpoint from disk and from every list that names it."""
    try:
        receipt = remove_imported_checkpoint(model_type)
    except KeyError:
        raise HTTPException(
            status_code=404, detail="No imported checkpoint is registered under that name."
        )
    removed = receipt["registrations"] or [receipt["model_type"]]
    receipt["cleared_preferences"] = _forget_everywhere(removed)
    return receipt
