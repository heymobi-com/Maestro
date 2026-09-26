"""Which Director shot is using a gallery file, and what to say about it.

Deleting the take a shot is using leaves a stale filename behind, and the rejoin
then refuses that shot with "Regenerate missing or invalid video clip(s) N before
rejoining" -- which reads as "re-render it" for what was only a deleted file.
Measured on a 150-shot project, that message sends the user to re-render a shot
that a re-pointed take could have filled.

Ours, so it lives here rather than inside ``launch.py``, the file upstream edits
in every release. The app endpoint keeps one call and one refusal.
"""

from __future__ import annotations

import json
import os

PIPELINE_PREFIX = "_director_pipeline_"
PIPELINE_SUFFIX = ".json"


def director_slot_using(out_dir: str, name: str) -> tuple[str, int, str] | None:
    """The Director shot that currently uses ``name``, if any.

    Returns ``(pipeline_id, clip_index, kind)``, where kind is what the file is to
    that shot: its rendered ``clip`` or its ``start image``.
    """
    try:
        entries = os.listdir(out_dir)
    except OSError:
        return None
    for entry in entries:
        if not (entry.startswith(PIPELINE_PREFIX) and entry.endswith(PIPELINE_SUFFIX)):
            continue
        try:
            with open(os.path.join(out_dir, entry), encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        pid = str(state.get("pipeline_id") or entry)
        for index, clip in enumerate(state.get("clips") or []):
            if not isinstance(clip, dict):
                continue
            if clip.get("video_filename") == name:
                return pid, index, "clip"
            if clip.get("start_image_filename") == name:
                return pid, index, "start image"
    return None


def director_slot_in_use_message(in_use: tuple[str, int, str]) -> str:
    """The refusal a 409 carries: which shot, and what deleting would cost."""
    pid, index, kind = in_use
    return (
        f"This file is the {kind} that Director project {pid} is using "
        f"for shot {index + 1}. If you delete it, the rejoin will use an "
        "earlier take of that shot when one exists, and will otherwise "
        "ask you to regenerate that shot. Delete it anyway?"
    )
