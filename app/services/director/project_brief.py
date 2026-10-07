"""The part of a project's brief that a single clip actually needs.

A Director clip renders as its own job, so the project's brief has to travel with it: without
it a clip loses the cast's faces, their wardrobe and who is allowed to lip-sync. But the brief
is also where the film's own sequence of events is written, and that part must not travel.

Measured on a real 30-clip project: every clip carried the whole 1,357-character
``DESCRIPTION`` section verbatim -- "at some point it rains, some creeper plants grow ...
then she comes back to the singer ... there's some slow dancing ... while the Camera does an
Orbit Shot". The model was therefore asked to perform the entire film inside every clip,
which is the reported "it loops all the instructions in every clip instead of building a
story". The plan already states which moment each clip covers, and that moment is in the
clip's own prompt, so the film's narrative is the one section that has to stay out.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["clip_project_context"]

# Headings that describe the FILM rather than the clip. Everything else travels: the cast and
# their wardrobe, the vocal roles, the audio rules and the visual world.
_NARRATIVE_HEADINGS = frozenset({
    "DESCRIPTION", "STORY", "NARRATIVE", "PLOT", "ARC", "SYNOPSIS", "STORYLINE",
    "SCRIPT", "SCREENPLAY", "OUTLINE", "BEATS", "SEQUENCE", "SCENE LIST",
    "SHOT LIST", "STORYBOARD", "SCENE BREAKDOWN",
})

# A ``HEADING:`` alone on its line. A section with no heading is a continuation of the
# previous one -- a subject row separated by a blank line has exactly that shape, and it
# carries an identity, so it stays.
_HEADING_RE = re.compile(r"^([A-Z][A-Z0-9 \-_/&']{2,40}):\s*$")


def clip_project_context(project_context: Any) -> str:
    """The project's brief without the sections that tell the whole film's story."""

    text = str(project_context or "").strip()
    if not text:
        return ""
    sections = [
        section.strip()
        for section in re.split(r"\n\s*\n", text)
        if section.strip()
    ]
    kept = [section for section in sections if not _is_film_narrative(section)]
    if len(kept) == len(sections):
        # Nothing to drop: returned unchanged, so a brief that never had a narrative
        # section is byte-for-byte what its author wrote.
        return text
    return "\n\n".join(kept)


def _is_film_narrative(section: str) -> bool:
    """Whether a section is headed as the film's story instead of the clip's contract."""

    first = section.splitlines()[0].strip()
    match = _HEADING_RE.match(first)
    return bool(match) and match.group(1).strip() in _NARRATIVE_HEADINGS
