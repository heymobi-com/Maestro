"""Publish each finished clip the moment it finishes.

The runtime kept the per-clip map of finished videos in a local variable and put it on the
job only after the last clip. A Stop or a power loss mid-batch therefore saved a state that
named no finished clip at all -- measured on a real project: 55 planned clips, 19 mp4s on
disk, 0 recorded, and every resume regenerated the film from its first frame.

Recording here keeps that contract next to the clips: each finished clip joins the job's own
record as soon as its file exists, which is what ``director_pipeline._persist_finished_clips``
mirrors into the saved state and what the cancelled path reads back through
``_clip_video_slots``.

The same stop also used to cost the clip its sidecar. Full sidecars are written when the job
finishes, so a cancelled run left finished mp4s with no ``.meta.json`` at all -- measured on
that run: five clips of a 26-shot film with no sidecar, so the gallery could not tell they
belonged to a Director project and the pencil offered them nothing. A minimal ownership
sidecar is written here instead: the completion path rewrites it with the full record, and if
the process dies first the clip still names the film it belongs to.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, MutableMapping, Optional

from services.job_lifecycle import record_job_outputs

__all__ = ["publish_finished_clip"]


def _write_ownership_sidecar(
    out_dir: Optional[str],
    job: MutableMapping[str, Any],
    clip_index: int,
    filename: str,
) -> bool:
    """Name the Director project on a clip that has no sidecar yet."""

    if not out_dir or not filename:
        return False
    params = job.get("params")
    if not isinstance(params, dict):
        return False
    pipeline_id = params.get("_director_pipeline_id")
    if not isinstance(pipeline_id, str) or not pipeline_id:
        return False
    meta_path = os.path.join(out_dir, os.path.splitext(filename)[0] + ".meta.json")
    if os.path.exists(meta_path):
        # The completion writer owns this file; never overwrite its timing.
        return False
    sidecar = {
        "params": {
            "_director_pipeline_id": pipeline_id,
            "_director_clip_index": int(clip_index),
        },
        "director_pipeline_id": pipeline_id,
        "director_artifact_kind": "clip",
        "director_clip_index": int(clip_index),
        "director_clip_settings_version": 1,
        # Marks this record as ownership only: the full recipe arrives with the
        # completion sidecar, or never if the run was stopped.
        "director_clip_settings_partial": True,
        "generation_mode": "video",
        "created_at": time.time(),
        "output_filename": filename,
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(sidecar, handle, indent=2)
    except OSError:
        return False
    return True


def publish_finished_clip(
    job: MutableMapping[str, Any],
    clip_index: int,
    filename: Optional[str],
    clip_output_files: dict[int, str],
    *,
    out_dir: Optional[str] = None,
) -> bool:
    """Bind one finished clip to its slot and put it on the job at once.

    ``clip_index`` is the position the render itself assigned. For a resumed batch that is
    the batch's own numbering, so the film position is the index plus the offset the caller
    recorded; that offset is applied wherever this record is read back.

    Returns whether the clip was recorded: an empty filename is not a clip.
    """

    if not filename:
        return False
    position = int(clip_index)
    clip_output_files[position] = filename
    record_job_outputs(job, [], clip_output_files={position: filename})
    _write_ownership_sidecar(out_dir, job, position, filename)
    return True
