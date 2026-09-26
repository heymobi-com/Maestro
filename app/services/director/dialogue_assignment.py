"""Place a transcript's rows on the timeline: one row, one shot, one line.

A project's words are not a creative decision. They are the authored script, or the
diarized audio, and the planner receives them as ``transcript`` rows. The viral and
podcast planners used to hand that job to the model instead -- they asked it to write
``dialogue_beats`` -- and a model under cognitive load writes some of them.

Measured on the written-script project the user kept re-running (20 authored rows, 14
clips): 20 rows, 31 row placements inside a window, and **6 beats** in the plan, with
**no beat carrying a speaker**. The rows were in the request and were simply ignored,
which is why the lines the user wrote did not reach the video.

Each row belongs to exactly one shot:

- the row's **midpoint** decides which. Turns and windows are both contiguous, so
  assigning by overlap alone would place a row that crosses a boundary in two shots
  and the line would be spoken twice;
- a row outside every window (a timeline shorter than its dialogue) lands in the
  nearest one rather than being dropped, because a lost line is the failure this
  module exists to prevent;
- a timeline with no usable timing at all gets no assignment, so the planner's own
  beats stand rather than every row landing in the first shot.

The model's own beats are replaced, not merged. Its staging is still used; its
retelling of the words is not, because a ``delivery`` on a line the user never wrote
is worth nothing.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

# "<d>[Spanish] words</d>" and "[Spanish] words" declare a language; a bare row does not.
_LEADING_TAG_RE = re.compile(r"^\s*(?:<d>\s*)?\[\s*([^\]\r\n]{1,40})\s*\]", re.IGNORECASE)
# Raw machine ids: a pyannote turn, or a synthesised character/subject slot.
_MACHINE_SPEAKER_RE = re.compile(r"^(?:speaker[_ -]?\d+|char[_ -]?\d+|subject[_ -]?\d+)$", re.IGNORECASE)


def _is_silent(value: Any) -> bool:
    """The compiler's own no-speech rule, so the two can never disagree.

    A first draft of this module kept its own list of markers and did not recognise
    ``<d>[silent]</d>``, which is the shape the planner actually writes -- the same
    mistake that once aborted a render with "dialogue contains an empty line".
    Imported lazily because the compiler is heavy and does not need this module.
    """

    from .h3_dialogue import is_silent_dialogue
    return is_silent_dialogue(value)


def row_span(row: Mapping[str, Any]) -> tuple[float, float]:
    """A transcript row's ``(start, end)``, tolerant of missing timings."""

    start = float(row.get("start", 0) or 0)
    end = float(row.get("end", start) or start)
    return start, end if end >= start else start


def windows(clips: Sequence[Mapping[str, Any]]) -> list[tuple[float, float]]:
    """The timing of each clip, which is what a row is placed against."""

    spans: list[tuple[float, float]] = []
    for clip in clips or []:
        if not isinstance(clip, Mapping):
            continue
        start, end = row_span(clip)
        spans.append((start, end))
    return spans


def timeline_has_timing(spans: Sequence[tuple[float, float]]) -> bool:
    """False when no window states a duration, so placement would be arbitrary."""

    return any(end > start for start, end in spans)


def row_language(row: Mapping[str, Any]) -> str:
    """The language a row declares, or ``""`` so the compiler may detect its own.

    Nothing is defaulted here. Labelling Spanish text as English is what makes the
    model read it with English phonetics, and a written row may legitimately carry no
    tag at all.
    """

    declared = str(row.get("language") or "").strip()
    if declared:
        return declared
    match = _LEADING_TAG_RE.match(str(row.get("text") or ""))
    if not match:
        return ""
    token = match.group(1).strip()
    # A no-speech marker has the shape of a language tag but is not one.
    return "" if _is_silent(f"[{token}]") else token


def speaker_name(row: Mapping[str, Any]) -> str:
    """The speaker a row states, whatever the source calls them."""

    return str(row.get("speaker") or row.get("speaker_name") or "").strip()


def beat_from_row(row: Mapping[str, Any]) -> dict:
    """One authored row as a dialogue beat.

    ``spoken_text`` keeps the row exactly as authored -- the H3 wrapper and its
    language tag are normalized later, at the boundary every planner beat crosses --
    and the language is also stated structurally, which outranks any inline tag.
    """

    beat: dict[str, Any] = {"spoken_text": str(row.get("text") or "").strip()}
    speaker = speaker_name(row)
    if speaker:
        beat["speaker_id"] = speaker
    language = row_language(row)
    if language:
        beat["language"] = language
    delivery = str(row.get("delivery") or "").strip()
    if delivery:
        beat["delivery"] = delivery
    return beat


def _usable_rows(rows: Optional[Sequence[Any]]) -> list[Mapping[str, Any]]:
    """The rows that carry words. A silent or blank row is not a line to place."""

    usable: list[Mapping[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        if _is_silent(row.get("text")):
            continue
        usable.append(row)
    return usable


def window_for(span: tuple[float, float], spans: Sequence[tuple[float, float]]) -> int:
    """The one window a row belongs to (see the module docstring for the rules)."""

    start, end = span
    middle = start + (end - start) / 2.0
    for index, (begin, finish) in enumerate(spans):
        if begin <= middle <= finish:
            return index

    best_index, best_overlap = -1, 0.0
    for index, (begin, finish) in enumerate(spans):
        overlap = min(end, finish) - max(start, begin)
        if overlap > best_overlap:
            best_index, best_overlap = index, overlap
    if best_index >= 0:
        return best_index

    return min(
        range(len(spans)),
        key=lambda index: abs(middle - (spans[index][0] + spans[index][1]) / 2.0),
    )


def assign_rows_to_clips(
    clips: Sequence[Mapping[str, Any]],
    rows: Optional[Sequence[Any]],
) -> list[list[dict]]:
    """One list of beats per clip, in row order. Every row lands in exactly one."""

    spans = windows(clips)
    buckets: list[list[dict]] = [[] for _ in spans]
    if not spans or not timeline_has_timing(spans):
        return buckets

    for row in _usable_rows(rows):
        index = window_for(row_span(row), spans)
        buckets[index].append(beat_from_row(row))
    return buckets


def beats_for_shot(
    authored: Sequence[Sequence[dict]],
    index: int,
    raw: Mapping[str, Any],
) -> list[dict]:
    """The authored rows for this shot, or the planner's own beats when it has none."""

    if index < len(authored) and authored[index]:
        return list(authored[index])
    fallback = (raw or {}).get("dialogue_beats") or []
    return [beat for beat in fallback if isinstance(beat, Mapping)]


def shot_speakers_note(
    authored: Sequence[Sequence[dict]],
    start: int,
    end: int,
) -> str:
    """Name each shot's speakers, so the model stages the people who actually speak.

    Only a name identifies a person to the model. A raw analysis id (``SPEAKER_00``)
    or a synthesised slot (``char_1``) says nothing, so those shots are left out and
    the model keeps deciding on its own, as it did before.
    """

    lines: list[str] = []
    for index in range(start, min(end, len(authored))):
        names: list[str] = []
        for beat in authored[index]:
            name = str(beat.get("speaker_id") or "").strip()
            if name and not _MACHINE_SPEAKER_RE.match(name) and name not in names:
                names.append(name)
        if names:
            lines.append(f"Shot {index + 1}: {', '.join(names)}")
    if not lines:
        return ""
    return (
        "\n\nSPEAKERS IN THESE SHOTS (for each one, add \"speaker_name\" with the name "
        "exactly as written here to a subjects_on_screen row that still carries its "
        "full visual_description; never write a row that is only a name):\n"
        + "\n".join(lines)
    )
