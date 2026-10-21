"""Keep MiniMax's Context-IR fields on their own lines.

A whitespace collapse (`re.sub(r"\\s+", " ", prompt)`) is the right repair for a
prompt whose edits left ragged line breaks, but it also deletes the boundaries
between the six Context-IR fields. The renderer reads the cast from
``subject_definitions`` with a line-anchored pattern, so a flattened prompt
declares no ``<Subject N> (Sx)`` pair at all and every spoken line is refused
with "could not determine which referenced character speaks".

Measured on a real short-film-audio run: the shot repair collapsed all eight
prompts onto one line, and every clip failed before the model loaded. This
helper puts the separators back, and never touches the text inside a field.
"""

from __future__ import annotations

import re

__all__ = ["H3_FIELD_ORDER", "restore_h3_field_layout"]

H3_FIELD_ORDER = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)

_FIELD_HEAD_RE = re.compile(
    r"(?<![A-Za-z_])(?:"
    + "|".join(H3_FIELD_ORDER)
    + r")\s*:",
    re.IGNORECASE,
)
_FIELD_INDEX = {name: index for index, name in enumerate(H3_FIELD_ORDER)}


def restore_h3_field_layout(text: str) -> str:
    """The prompt with a separator before each Context-IR field, or ``text``.

    A head is only treated as a boundary when it appears *after* the previously
    accepted head in the canonical order, so the word "summary" beside ordinary
    prose cannot split the prompt. Text already laid out is returned unchanged.
    """

    value = str(text or "")
    if not value:
        return value
    boundaries: list[int] = []
    last_index = -1
    for match in _FIELD_HEAD_RE.finditer(value):
        name = match.group(0).rstrip().rstrip(":").strip().casefold()
        index = _FIELD_INDEX.get(name)
        if index is None or index <= last_index:
            continue
        last_index = index
        if match.start() > 0:
            boundaries.append(match.start())
    if len(boundaries) < 2:
        # One field word in ordinary prose is not a Context-IR layout, and
        # splitting it would insert a line break nobody asked for.
        return value
    restored = value
    for position in reversed(boundaries):
        restored = restored[:position].rstrip() + "\n\n" + restored[position:]
    return restored
