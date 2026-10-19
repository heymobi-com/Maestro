"""Which visible Subject a reference picture belongs to.

A reference's ``role`` is whatever the Director wrote: a character's name, a speaker token such as
``S1``, or a phrase that only echoes one of them. Upstream maps that with
``_mapped_ref2va_subject``, which reads the explicit names and the inferred identities of each
cast row. That mapping is right when the row carries an identity, and it is empty when the row
names its participant *only* through the inline ``(Sx)`` label that the shot's own body uses.

Falling through to a brand-new ``<Subject N>`` in that case gave one participant two labels -- one
for acting and one for identity -- and the identity picture then never reached the person it
described: measured on a shot whose two rows were written ``<Subject 1> (S1), the woman in a cream
A-dress`` and its companion, the compiled prompt declared four subjects for two people.

So this is the second half of the answer, stated once and applied only when the first half found
nothing: match the role against the row's inline label, then against the labels the body binds to
its Subjects, then give the role itself the slot it is already using.

The callables are passed in rather than imported so this module stays a leaf: the planner's slot
rules, the registry lookup and the body bindings all live in ``h3_dialogue``, and importing back
from here would make the dependency circular.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

__all__ = ["planner_subject_for_role"]

_SLOT_TYPE = tuple[int, str, str]


def planner_subject_for_role(
    role: str,
    subjects: Sequence[Any],
    *,
    registry: Mapping[str, Any],
    speaker_slots: Mapping[str, int],
    planner_slot: Callable[[Any, int], tuple[int, str]],
    registry_entry: Callable[[Mapping[str, Any], str], tuple[str, str] | None],
    field: Callable[[Any, str, Any], Any],
    normalize: Callable[[Any], str],
) -> _SLOT_TYPE | None:
    """The ``(slot, name, stable_id)`` a reference role addresses, or ``None``.

    ``slot`` is the number the planner already uses for that participant, so the identity
    definition lands on the Subject the action text names and no second Subject is minted.
    """

    folded_role = str(role or "").casefold()
    for subject_index, subject in enumerate(subjects or [], start=1):
        slot, inline_speaker = planner_slot(subject, subject_index)
        character_id = normalize(field(subject, "character_id", ""))
        subject_name = normalize(field(subject, "speaker_name", "") or character_id)
        candidates = [
            value for value in (subject_name, character_id)
            if len(value) >= 2
        ]
        if inline_speaker and inline_speaker.casefold() in folded_role:
            candidates.append(inline_speaker)
        if not any(value.casefold() in folded_role for value in candidates):
            continue
        entry = registry_entry(registry, character_id or subject_name)
        stable_id = entry[0] if entry else ""
        return slot, subject_name or character_id, stable_id
    # No row matched: the planner's own binding still says which Subject the shot uses.
    for speaker, slot in (speaker_slots or {}).items():
        if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(str(speaker))}(?![0-9])",
            str(role or ""),
            re.IGNORECASE,
        ):
            return slot, str(role or ""), ""
    return None
