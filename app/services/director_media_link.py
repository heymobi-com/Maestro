"""Which Director project a rendered file belongs to, when its sidecar cannot say.

A gallery item learns its project from the sidecar written next to it: the generation job
records ``director_pipeline_id`` there, and the gallery's open-project route reads it back.
A missing sidecar therefore leaves a perfectly visible render -- its prompt travels inside
the media file -- with no route back to the project that made it. That is what "the videos
are in the gallery but the Dashboard does not connect them to their project" looks like.

The file still knows. Director stages each project's audio and reference images under
``_director_assets/<pid>/``, and those paths are part of the parameters stored in the
media. Measured over the 89 videos in this install that have no sidecar:

- ``_director_pipeline_id`` inside the file: 0 of the 38 whose project still exists, so the
  generator does not record it there and it cannot be the rule;
- ``multi_clip_info`` (a multi-clip batch, which a plain Studio render does not record):
  35 of 38, so it would lose three genuine Director renders;
- a ``_director_assets/<pid>/`` path: 38 of 38.

So the paths are what the fallback reads, and the project that staged the render wins on
count: the owner appears in several paths (its audio, its references, its joined audio),
while a file borrowed from another project appears in one.

An existing ``director_pipeline_id`` is never overwritten. The sidecar is the record of
record and this only fills a gap.

A derived id is attached only when the project is still on disk, and that is the crux. The
front end reads the presence of ``director_pipeline_id`` as "this render came from Director"
and its Load settings button then opens that project instead of applying the parameters. A
link to a deleted project would take that button over and fail, on a render whose parameters
are perfectly loadable -- so a derived id with no project behind it must not be attached.

Only the id is derived, never the shot's position in the film. The gallery keeps a film
together by sorting on ``director_clip_index``, and the batch index recorded in the media is
not that number: a resumed Director run numbers its batch from zero again, and using it would
file those shots in the wrong slot -- the bug the offset comment in launch.py records. With
no index the gallery sorts the item normally, so filling in the id alone changes the feed not
at all.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterator

# The id is hex and exactly as long as the folder name Director creates for it; the
# lookahead stops a longer hex run from being read as an 8-character id.
_ASSET_PROJECT = re.compile(r"_director_assets[\\/]+([0-9a-f]{8})(?![0-9a-f])")

_STATE_PREFIX = "_director_pipeline_"

# Parameters nest (references hold their own dicts), but deep enough is deep enough: a
# cycle or a runaway tree must not turn a gallery request into an infinite walk.
_MAX_DEPTH = 12


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    """Every string inside a parameter tree, at any depth."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item, depth + 1)


def project_id_from_params(params: Any) -> str:
    """The Director project a file's own parameters name, or "" when they name none."""
    if not isinstance(params, dict):
        return ""
    recorded = str(params.get("_director_pipeline_id") or "").strip()
    if recorded:
        return recorded
    counts: dict[str, int] = {}
    for text in _strings(params):
        for found in _ASSET_PROJECT.findall(text):
            counts[found] = counts.get(found, 0) + 1
    if not counts:
        return ""
    # Ties keep the first appearance: max() returns the first maximal key in insertion
    # order, and insertion order is the order the paths were walked.
    return max(counts, key=lambda pid: counts[pid])


def project_state_exists(out_dir: str, pid: str) -> bool:
    """Whether the project's state file is still beside the media it produced.

    A pipeline writes ``_director_pipeline_<pid>.json`` in the folder it rendered into, so
    this is one stat call where the video lives. A render moved to another workspace is not
    linked rather than linked wrongly.
    """
    if not pid or not out_dir:
        return False
    return os.path.isfile(os.path.join(out_dir, f"{_STATE_PREFIX}{pid}.json"))


def attach_project_link(metadata: Any, out_dir: str = "") -> Any:
    """Give metadata the project its file came from, when the metadata has none.

    Returns the same object, so a caller can wrap an existing expression. An id already
    present -- the sidecar's -- is left exactly as it was; a derived one is attached only
    if ``out_dir`` still holds the project's state file. See the module docstring for why
    that condition is not optional.
    """
    if not isinstance(metadata, dict) or metadata.get("director_pipeline_id"):
        return metadata
    project = project_id_from_params(metadata.get("params"))
    if project and project_state_exists(out_dir, project):
        metadata["director_pipeline_id"] = project
    return metadata
