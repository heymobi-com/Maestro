"""Cut markers a single-shot body must not keep.

The H3 guide tells the planner to open with ``[Shot 1]`` and no timestamp, and the compiler
already rewrites director-authored cut markers to the clip's real audio window and then collapses
a body to one shot. Neither step reaches a timestamp the planner wrote *loose* in the prose,
because the rewrite only matches the ``[Shot N] At ...`` shape and the collapse only has a marker
to remove when there is one.

Measured on a real 30-shot project, in the compiled prompts the engine actually rendered: 11
clips carried one loose marker each -- ``At MM:04.000``, ``MM:08.200``, ``MM:12.400`` and so on,
roughly four seconds apart -- while those clips sit at 88 s, 94 s and 99 s of the film. A
timestamp four seconds into a seven-second clip reads as a cut inside the clip, which is how one
shot becomes a fast succession of everything it was supposed to spread across the film: the
planner's own invented clock, handed to the renderer as if it were a second shot.

A body that holds one shot has no later cut, so the marker goes, and so does the separator it
leaves behind. Only the official ``MM:SS.mmm`` marker shape is touched: ``at 3.5s`` in ordinary
prose is somebody's timing note, not a section label, and it is left alone.
"""

from __future__ import annotations

import re

__all__ = ["strip_loose_cut_times"]

# The official marker, with or without the ``[Shot N]`` label the rewrite expects, and either
# the digits a planner substitutes or the literal ``MM:`` placeholder it sometimes copies.
_LOOSE_MARKER = re.compile(
    r"\s*\bAt\s+(?:MM:|\d{1,2}:)\d{2}(?:\.\d{1,3})?\s*(?=[,.;:]|\s|$)",
    re.IGNORECASE,
)
_CLEANUP = (
    (re.compile(r"[ \t]{2,}"), " "),
    # The marker sat between a sentence and its continuation: "<Picture 1>. At MM:04.000,
    # Roman ..." must not come back as "<Picture 1>., Roman ...".
    (re.compile(r"([.;:])\s*,"), r"\1"),
    (re.compile(r",\s*([.;:])"), r"\1"),
    (re.compile(r"\s+([,.;:])"), r"\1"),
    (re.compile(r"\.\s*\."), "."),
    (re.compile(r",\s*,"), ","),
)


def strip_loose_cut_times(body: str) -> tuple[str, int]:
    """Remove timestamps that would read as a cut inside a one-shot body.

    Returns the text and how many markers were removed, so a caller can report them.
    """

    text = str(body or "")
    if not text:
        return text, 0
    cleaned, removed = _LOOSE_MARKER.subn("", text)
    if not removed:
        return text, 0
    for pattern, replacement in _CLEANUP:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned, removed
