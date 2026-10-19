"""Which clips a stopped run already has, decided from what outlives the process.

A resume slices the batch at the first clip without a video, and that list lived only in
``_pipelines[pid]["_clip_video_files"]`` -- process memory. Closing the app therefore made
every resume start from clip 1 while the finished mp4s sat on disk. Measured on one real
project: 55 planned clips, **19 mp4 files in the folder, 0 detected**, and the
"Resume: N clip(s) already rendered" line never printed, which is how the failure reads in the
log. The work was never lost; only its record was.

So the record is taken from what survives a restart, in order of authority:

* the saved state's per-clip ``video_filename`` (what ``_persist_finished_clips`` writes through
  ``_save_pipeline_state``), and
* the state's ``_clip_video_files`` list, when an older state carries that shape, and
* finally the in-memory list the caller already has, which is what covers the run that is still
  going in this process.

Every entry is checked against the filesystem before it is trusted: a state file can outlive the
media it names, and a clip that does not exist must still be rendered.

One thing is deliberately not done: the clip index a render records is not the film's index. A
resumed run numbers its own batch from zero again (the offset ``launch.py`` records), so a
position is taken from the state's own clip list, never derived from a filename.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Iterable, Optional


def director_clip_offset(params: Any) -> int:
    """The film position a resumed batch starts at; zero when it states none.

    A stopped run is resumed as a batch that numbers its clips from zero again, so this offset
    is what turns a batch position into a film position -- the number the gallery sorts on and
    the number the sidecar records. It travels as a job parameter and a saved project's JSON can
    hold it as text.
    """

    try:
        return int((params or {}).get("_director_clip_offset") or 0)
    except (TypeError, ValueError):
        return 0


def publish_position(params: Any) -> tuple[Any, Any, int]:
    """Where a finished clip sits in its film, and which take it replaces.

    Three readings the batch needs when it writes a sidecar: a single-clip rerun publishes one
    output and no ``clip_output_files`` map, so it states its own position; a rerun names the
    take it supersedes, which the gallery stacks the new one above; and a resumed batch numbers
    from zero again, so the offset is the film position. They travel together because they answer
    one question -- where does this render belong -- and reading them apart is how a rerun lands
    in the wrong slot.
    """

    values = params or {}
    return (
        values.get("_director_clip_index"),
        values.get("_director_supersedes"),
        director_clip_offset(values),
    )


def _state_clip_videos(state: dict[str, Any]) -> list[str]:
    """The clip videos a saved state names, in film order."""

    named = [
        str(clip.get("video_filename") or "")
        for clip in (state.get("clips") or [])
        if isinstance(clip, dict)
    ]
    if any(named):
        return named
    listed = state.get("_clip_video_files") or []
    return [str(entry or "") for entry in listed] if isinstance(listed, list) else []


def load_saved_clip_videos(out_dir: str, pid: str) -> list[str]:
    """Read the finished clips out of the project's saved state, if it has one."""

    if not out_dir or not pid:
        return []
    pattern = os.path.join(out_dir, f"_director_pipeline_{pid}*.json")
    for path in sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict):
            videos = _state_clip_videos(state)
            if any(videos):
                return videos
    return []


def completed_clip_video_prefix(
    pid: str,
    clip_count: int,
    out_dir: str,
    in_memory: Optional[Iterable[Any]] = None,
) -> list[Optional[str]]:
    """Clip videos a stopped run already has, one slot per clip, holes included.

    The caller passes what this process knows; the saved state covers the run that a restart
    took away. A name is only reported where the file is really there.
    """

    if clip_count <= 0:
        return []
    # Both records are in film coordinates: ``_persist_finished_clips`` adds the run's offset
    # before it stores a position, in memory and in the state alike. So they combine by
    # position -- the live one wins where it names a clip, the state covers what a restart
    # took away -- and neither is ever indexed by a filename.
    candidates = _merge(
        [str(entry or "") for entry in (in_memory or [])],
        load_saved_clip_videos(out_dir, pid),
    )
    slots: list[Optional[str]] = [None] * clip_count
    for index, filename in enumerate(candidates[:clip_count]):
        if filename and os.path.isfile(os.path.join(out_dir, filename)):
            slots[index] = filename
    # An empty record answers "nothing to reuse", the length-zero list the caller (and the
    # Dashboard) already reads as "no prefix"; one slot per clip is only reported once some
    # clip is really reusable.
    return slots if any(slots) else []


def _merge(first: list[str], second: list[str]) -> list[str]:
    """One clip record, taking whichever side names each position."""

    length = max(len(first), len(second))
    merged = [""] * length
    for index in range(length):
        merged[index] = (
            (first[index] if index < len(first) else "")
            or (second[index] if index < len(second) else "")
        )
    return merged
