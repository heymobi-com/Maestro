"""Repair the two structural faults the project audit reports, without judgement calls.

The audit is read-only on purpose; this module is the step that writes, so it is careful about
what it claims. It fixes only the two faults whose correct answer is already written down by
the project itself:

  * ``subject-field-duplicated`` -- ``subject_definitions:`` emitted once per participant. The
    field declares the cast once, so the extra heads are folded into the first one.
  * ``subject-swap`` -- a participant given the wrong ``<Subject N>``. The project's own
    SUBJECT LOCK ("<Subject 1> es SIEMPRE Valeria") says which number each participant has, so
    the labels are renumbered to it. The participant's description stays with the participant:
    the number moves, the prose does not.

Measured on a 177-shot project: the compiler-side fix cleared 48 of 56 swaps for the shots it
recompiles, and left 8 -- exactly the shots whose saved text is already a compiled prompt, which
the compiler renders verbatim and never renumbers. Those are data, and these are the two
repairs they need. Nothing here guesses who says a line: a line whose cue names the wrong
participant is reported by the audit for the director to decide.
"""

from __future__ import annotations

import re
from typing import Any

from services.director.h3_dialogue import project_subject_lock
from services.director.prompt_audit import _SUBJECT_FIELD_RE, subject_block

# A subject entry inside the head. The dialects found in the wild:
#     "<Subject 1> (S1): Ricardo , leaning in"      the compiler's own output
#     "<Subject 1> is Ricardo (S1): Ricardo"        an older planner
#     "<Subject 1>: Ricardo, wearing a sweater"     no (Sx) label at all
# The name is captured so the entry can be renumbered by who it describes rather than by where
# it sits. Leaving the (Sx) label optional matters: the third dialect was invisible to this
# pattern, so those shots kept their swapped numbering after a repair that reported success.
_SUBJECT_ENTRY_RE = re.compile(
    r"<Subject\s*(\d+)>\s*(?:(is\s+)?\(?\s*S?\s*(\d+)\s*\)?\s*)?[:=]?\s*"
    r"([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+)",
    re.IGNORECASE,
)

_NEXT_FIELD_RE = re.compile(
    r"(?m)^[ \t]*(?:summary|retention_analysis|detailed_description|"
    r"integrated_multimodal_description|overall_soundscape|non_diegetic_music)[ \t]*:"
)

def _head_span(text: str) -> tuple[int, int]:
    """Where the ``subject_definitions`` block starts and ends."""

    block = subject_block(text)
    if not block:
        return -1, -1
    start = str(text).find(block)
    return start, start + len(block)


def _renumber_entry(match: re.Match, lock: dict[int, str]) -> str:
    """The same entry with the participant's number replaced, and nothing else touched.

    Only the two places that carry the number are rewritten -- the identity tag and the label
    beside it -- so spacing, punctuation and the description survive byte for byte. The first
    attempt rebuilt the entry from its capture groups and produced
    ``<Subject 2>(2)Ricardo`` with the colon and spacing lost.
    """

    person = match.group(4)
    wanted = next(
        (int(number) for number, name in lock.items() if str(name).casefold() == person.casefold()),
        None,
    )
    if wanted is None:
        return match.group(0)
    entry = match.group(0)
    entry = re.sub(r"(?i)<\s*Subject\s*\d+\s*>", f"<Subject {wanted}>", entry, count=1)
    entry = re.sub(r"(?i)(\()\s*S\s*\d+\s*(\))", rf"\g<1>S{wanted}\g<2>", entry, count=1)
    return entry


def subject_label_skeleton(text: str) -> str:
    """The text with every Subject number blanked out.

    Two texts with the same skeleton differ only in which number each participant carries --
    which is exactly what a renumbering is allowed to change and what this module must not
    exceed. It is the check the first attempt lacked: comparing only the text outside the cast
    block let a rewrite that deleted a blank separator (and so joined two Context-IR fields)
    pass as a safe one.
    """

    skeleton = re.sub(r"(?i)<\s*Subject\s*\d+\s*>", "<Subject>", str(text or ""))
    return re.sub(r"(?i)\(\s*S\s*\d+\s*\)", "(S)", skeleton)


def normalize_subject_block(text: str, project_context: str) -> str:
    """Renumber each participant to the project's lock, leaving every other byte alone.

    Returns the text unchanged when the project declares no lock, because then there is no
    authority saying which number a participant should have.

    A duplicated ``subject_definitions`` head is left for the director: folding two heads into
    one is a reformatting, and a reformatting that loses a separator joins two Context-IR
    fields, which is worse than the duplication it set out to fix.
    """

    source = str(text or "")
    lock = project_subject_lock(project_context)
    start, end = _head_span(source)
    if start < 0 or not lock:
        return source
    head = _SUBJECT_ENTRY_RE.sub(lambda match: _renumber_entry(match, lock), source[start:end])
    return source[:start] + head + source[end:]


def normalize_clip_prompts(clip: Any) -> dict[str, str]:
    """The fields of a clip that need rewriting, keyed by field name. Empty when it is clean.

    Both the saved prompt and the draft are offered because either can be what renders: a
    draft that is already a compiled prompt is rendered verbatim, while a loose draft is
    compiled again and its head is rebuilt from the plan.
    """

    context = str((clip or {}).get("_director_project_context") or "")
    changed: dict[str, str] = {}
    if not project_subject_lock(context):
        return changed
    for field in ("video_prompt", "_director_h3_source_prompt"):
        current = str((clip or {}).get(field) or "")
        if not current or not _SUBJECT_FIELD_RE.search(current):
            continue
        fixed = normalize_subject_block(current, context)
        if fixed != current:
            changed[field] = fixed
    return changed
