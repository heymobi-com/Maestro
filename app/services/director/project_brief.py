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

__all__ = ["audit_project_brief", "clip_project_context"]

# Headings that describe the FILM rather than the clip. Everything else travels: the cast and
# their wardrobe, the vocal roles, the audio rules and the visual world.
_NARRATIVE_HEADINGS = frozenset({
    "DESCRIPTION", "STORY", "NARRATIVE", "PLOT", "ARC", "SYNOPSIS", "STORYLINE",
    "SCRIPT", "SCREENPLAY", "OUTLINE", "BEATS", "SEQUENCE", "SCENE LIST",
    "SHOT LIST", "STORYBOARD", "SCENE BREAKDOWN",
})

# A ``HEADING:`` on a line of its own, short enough that a prose sentence ending in a colon
# is not mistaken for one. Parentheses and lower case are allowed: a director writes
# ``SUBJECT LOCK (critical, non-negotiable):`` and ``Description:`` and both are headings.
_HEADING_RE = re.compile(r"^([^\n:]{2,60}):\s*$")


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
    return bool(match) and match.group(1).strip().upper() in _NARRATIVE_HEADINGS


def _sections(text: Any) -> list[str]:
    return [section.strip() for section in re.split(r"\n\s*\n", str(text or "")) if section.strip()]


def _label(section: str) -> str:
    """A section's heading, or a visible marker when it has none.

    A block with no heading is copied into every shot, and a director who cannot see that
    in the report has no way to know the film's own prose is riding along.
    """

    first = section.splitlines()[0].strip()
    match = _HEADING_RE.match(first)
    return match.group(1).strip() if match else "(no heading)"


def audit_project_brief(
    project_context: Any,
    *,
    used_subjects: Any = (),
) -> dict[str, Any]:
    """What the project's own text will do to the shots, before any model is loaded.

    The brief has two readers with different limits: the planner takes it whole, while each
    shot's prompt carries at most the compiler's context budget. A director writing that text
    has no way to know which of their sections survive, so they are named here: what travels
    into every shot, what the film's story part is, and what the budget will drop.

    Nothing is changed. This reports and stops.
    """

    # Imported here: h3_dialogue imports this module, so a module-level import would be a
    # cycle. The budget and the packer have to be the compiler's own, not a second copy.
    from services.director.h3_dialogue import _H3_CONTEXT_BUDGET, pack_project_context

    text = str(project_context or "").strip()
    lock = _subject_lock(text)
    subjects = {int(value) for value in used_subjects or () if str(value).isdigit()}
    result: dict[str, Any] = {
        "chars": len(text),
        "budget": int(_H3_CONTEXT_BUDGET),
        "travelling_chars": 0,
        "travelling": [],
        "dropped": [],
        "story": [],
        "subject_lock": lock,
        "notes": [],
    }
    if not text:
        result["notes"].append("the project text is empty")
        return result

    # The compiler's own order: the film's story leaves first, then the budget packs what is
    # left. Reusing both keeps this report true to what a shot actually carries.
    trimmed = clip_project_context(text)
    packed = pack_project_context(trimmed)
    travelling_labels = {_label(section) for section in _sections(packed)}
    result["travelling"] = [
        [_label(section), len(section)] for section in _sections(packed)
    ]
    result["dropped"] = [
        [_label(section), len(section)]
        for section in _sections(trimmed)
        if _label(section) not in travelling_labels
    ]
    result["story"] = [
        [_label(section), len(section)]
        for section in _sections(text)
        if section not in _sections(trimmed)
    ]
    result["travelling_chars"] = sum(size for _, size in result["travelling"])

    if len(subjects) >= 2 and not lock:
        result["notes"].append(
            f"no SUBJECT LOCK while the plan uses {len(subjects)} subjects: which "
            "participant is which <Subject N> is being inferred"
        )
    if result["dropped"]:
        result["notes"].append(
            f"the {int(_H3_CONTEXT_BUDGET)}-character budget for a shot drops "
            f"{len(result['dropped'])} section(s) from this project text"
        )
    unheaded = [entry for entry in result["travelling"] if entry[0] == "(no heading)"]
    if unheaded:
        result["notes"].append(
            f"{unheaded[0][1]} characters with no section heading are copied into every "
            "shot; give the block a heading so it can be identified"
        )
    return result


def _subject_lock(project_context: str) -> dict[int, str]:
    """The SUBJECT LOCK map, read through the compiler's own parser."""

    if not project_context:
        return {}
    from services.director.h3_dialogue import project_subject_lock

    return {
        int(number): name
        for number, name in project_subject_lock(project_context).items()
    }
