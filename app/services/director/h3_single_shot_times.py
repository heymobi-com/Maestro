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
# A marker that carries its own label is left alone: that is the shape the compile step localizes
# to this clip's window ("[Shot 5] At 00:00.000" for a clip that opens the film becomes
# "[Shot 1] At 00:00.000"), and only an unlabelled timestamp is a planner clock that cannot be
# reconciled with anything.
_LOOSE_MARKER = re.compile(
    r"(?P<label>\[\s*Shot\s+\d+\s*\]\s*)?"
    r"\s*\bAt\s+(?P<time>(?:MM:|\d{1,2}:)\d{2}(?:\.\d{1,3})?)\s*(?=[,.;:]|\s|$)",
    re.IGNORECASE,
)
_LOCAL_TIME_RE = re.compile(r"(?P<minutes>\d{1,2}):(?P<seconds>\d{2})(?:\.(?P<fraction>\d{1,3}))?")


def _local_seconds(value: str) -> float | None:
    """Read ``MM:SS[.mmm]`` as seconds; ``MM:`` placeholders are unknown."""

    match = _LOCAL_TIME_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    fraction = match.group("fraction") or ""
    return (
        int(match.group("minutes")) * 60
        + int(match.group("seconds"))
        + (int(fraction) / (10 ** len(fraction)) if fraction else 0.0)
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

# A timestamp a cue introduces belongs to the source, not to a cut: "The source audio cue: At
# 00:08.000 anchors the lyric" states where the supplied audio sits, and retiming or removing it
# would move the sync point the render is built on.
_CUE_PREFIX_RE = re.compile(
    r"(?:source\s+audio|audio|music|song|lyric|lyrics|source)?\s*cue\s*:?\s*$"
    r"|(?:source\s+audio|lyrics?|song)\s*:?\s*$",
    re.IGNORECASE,
)


_DIALOGUE_BLOCK_RE = re.compile(r"<d>.*?</d>", re.DOTALL)


def strip_loose_cut_times(
    body: str,
    duration_seconds: float | None = None,
) -> tuple[str, int]:
    """Remove timestamps that would read as a cut inside a one-shot body.

    Returns the text and how many markers were removed, so a caller can report them.

    ``duration_seconds`` turns a labelled marker past the end of the clip into a
    removal as well. A planner that keeps the film's own clock writes
    ``[Shot 3] At 00:10.400`` into a 5.167-second clip, where no second shot can
    exist; the renderer would either cut early or refuse the shot, and the clip
    is one continuous shot either way.
    """

    text = str(body or "")
    if not text:
        return text, 0
    # A <d> block is spoken content. A line can say a timestamp out loud ("Read At 00:09.800
    # aloud"), and dropping it there would change the words the contract compares against the
    # plan's dialogue beats -- measured: the line became "Read aloud." and the compile refused.
    spoken_spans = [
        (match.start(), match.end()) for match in _DIALOGUE_BLOCK_RE.finditer(text)
    ]
    removed = 0

    def _beyond_clip(match: re.Match[str]) -> bool:
        if duration_seconds is None:
            return False
        value = _local_seconds(match.group("time"))
        if value is None:
            return False
        return value > max(0.0, float(duration_seconds)) + 0.05

    def _drop(match: re.Match[str]) -> str:
        nonlocal removed
        if any(start <= match.start() < end for start, end in spoken_spans):
            return match.group(0)
        if match.group("label") and not _beyond_clip(match):
            return match.group(0)
        if _CUE_PREFIX_RE.search(text[max(0, match.start() - 48):match.start()]):
            return match.group(0)
        removed += 1
        return ""

    cleaned = _LOOSE_MARKER.sub(_drop, text)
    if not removed:
        return text, 0
    for pattern, replacement in _CLEANUP:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned, removed
