"""The whole timeline, shown to a batch that plans only part of it.

A long music video is planned in bounded batches, and a batch used to see only its own clips with
the previous ending as its only reference. Measured on the reported project, that is what made the
middle of a film repeat the staging the earlier batches had already used: the model had no way to
know what was still ahead, so it re-invented it.

The overview is context, not a request: the paragraph names the batch's own range twice, because a
model handed the whole list writes every clip in it.

This lives outside ``planners/music_video.py`` because 3.0 rewrote that batch prompt, and the seam
guard counts retained fork repairs inside upstream-owned files line by line.
"""

from __future__ import annotations

from typing import Sequence

__all__ = ["whole_timeline_context"]


def whole_timeline_context(
    start: int,
    end: int,
    clip_contexts: Sequence[str],
) -> str:
    """The overview paragraph appended to a long-form batch's prompt.

    ``start`` and ``end`` are zero-based: the batch plans global clips ``start + 1`` to ``end``.
    """

    return (
        "\n\nTHE WHOLE TIMELINE (context only: see where this batch sits "
        f"and what is still ahead; plan ONLY clips {start + 1}-{end}):\n"
        + "\n".join(clip_contexts)
    )
