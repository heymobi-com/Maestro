"""Publish each finished clip the moment it finishes.

The runtime kept the per-clip map of finished videos in a local variable and put it on the
job only after the last clip. A Stop or a power loss mid-batch therefore saved a state that
named no finished clip at all -- measured on a real project: 55 planned clips, 19 mp4s on
disk, 0 recorded, and every resume regenerated the film from its first frame.

Recording here keeps that contract next to the clips: each finished clip joins the job's own
record as soon as its file exists, which is what ``director_pipeline._persist_finished_clips``
mirrors into the saved state and what the cancelled path reads back through
``_clip_video_slots``.
"""

from __future__ import annotations

from typing import Any, MutableMapping, Optional

from services.job_lifecycle import record_job_outputs

__all__ = ["publish_finished_clip"]


def publish_finished_clip(
    job: MutableMapping[str, Any],
    clip_index: int,
    filename: Optional[str],
    clip_output_files: dict[int, str],
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
    return True
