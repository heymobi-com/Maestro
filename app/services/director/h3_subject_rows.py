"""One row per person, when the planner packed or duplicated participants.

Both repairs read the planner's ``_director_subjects_on_screen`` rows and key them by the
``(Sx)`` label the row carries, because that label -- not the row order -- is what identifies the
person. What they prevent is identical in shape: a row that carries two people, or the same person
carried twice, allocated a Subject of its own, so a two-person project compiled three or four
Subjects and an identity picture reached none of the people it described.

They live here rather than in ``h3_dialogue`` because v3 rewrote most of that file and the seam
guard counts retained repairs inside upstream-owned files line by line.
"""

from __future__ import annotations

import re
from typing import Any, MutableMapping, Sequence

__all__ = [
    "SPEAKER_TOKEN_RE",
    "merge_subject_rows_for_the_same_speaker",
    "split_multi_speaker_subject_rows",
]

SPEAKER_TOKEN_RE = re.compile(r"\(\s*S\s*(\d+)\s*\)", re.IGNORECASE)


def _text_field(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, MutableMapping):
        return value.get(key, default)
    return getattr(value, key, default)


def split_multi_speaker_subject_rows(
    clip_plans: Sequence[MutableMapping[str, Any]],
) -> None:
    """One row per person when the planner packed two into a single row.

    A real row read ``"Valeria (S1), brunette, navy blazer / Ricardo (S2),
    mature man in grey sweater & glasses."`` -- two people carrying ONE label.
    Left alone, the compiler emitted a duplicate entry for the first speaker and
    then minted a fresh ``<Subject 3>`` to bind the second person's identity
    picture, so a project with two participants rendered a third character.
    Nothing in the planner's own output named three people.
    """

    for plan in clip_plans:
        rows = plan.get("_director_subjects_on_screen")
        if not isinstance(rows, list) or not rows:
            continue
        rebuilt: list[Any] = []
        for row in rows:
            if not isinstance(row, MutableMapping):
                rebuilt.append(row)
                continue
            text = str(_text_field(row, "visual_description", "") or "")
            if len(SPEAKER_TOKEN_RE.findall(text)) < 2:
                rebuilt.append(row)
                continue
            fragments = [
                fragment.strip()
                for fragment in re.split(r"\s*(?:/|;|\||\n|\u2022)\s*", text)
                if fragment.strip()
            ]
            # A fragment with no label of its own belongs to the person named
            # before the separator, so it is merged back into their row.
            grouped: list[str] = []
            for fragment in fragments:
                if not grouped or SPEAKER_TOKEN_RE.search(fragment):
                    grouped.append(fragment)
                else:
                    grouped[-1] = f"{grouped[-1]}, {fragment}"
            labelled = [
                fragment for fragment in grouped
                if SPEAKER_TOKEN_RE.search(fragment)
            ]
            if len(labelled) < 2:
                rebuilt.append(row)
                continue
            for fragment in grouped:
                clone = dict(row)
                clone["visual_description"] = fragment
                rebuilt.append(clone)
        plan["_director_subjects_on_screen"] = rebuilt


def merge_subject_rows_for_the_same_speaker(
    clip_plans: Sequence[MutableMapping[str, Any]],
) -> None:
    """One row per person when the planner described the same one twice.

    Clip 25 listed ``"Valeria (S1), brunette, navy blazer"`` and then a second
    row ``"Valeria (S1)"``. Two rows for one label became two subject slots, and
    the spare one was emitted as ``<Subject 3> (S1): Valeria`` -- a third
    character for a project with two participants. Rows are keyed by the label
    they carry, because that is what identifies the person.
    """

    for plan in clip_plans:
        rows = plan.get("_director_subjects_on_screen")
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        merged: list[Any] = []
        seen: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, MutableMapping):
                merged.append(row)
                continue
            token = SPEAKER_TOKEN_RE.search(
                str(_text_field(row, "visual_description", "") or "")
            )
            key = token.group(1).casefold() if token else ""
            if not key or key not in seen:
                if key:
                    seen[key] = len(merged)
                merged.append(row)
                continue
            # Keep the fuller description; a restated stub adds no detail.
            previous = merged[seen[key]]
            current_text = str(_text_field(row, "visual_description", "") or "")
            previous_text = str(_text_field(previous, "visual_description", "") or "")
            if len(current_text) > len(previous_text):
                merged[seen[key]] = row
        plan["_director_subjects_on_screen"] = merged
