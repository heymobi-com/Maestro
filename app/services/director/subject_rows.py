"""One model-authored subject row, read without trusting it.

`SubjectRef.from_dict` was the only place in the Director schema that indexed a key
straight out of model output:

    File "app/services/director/schema.py", line 157, in from_dict
        visual_description=d["visual_description"],
    KeyError: 'visual_description'

That `KeyError` aborted a real render at planning time. The model had written a row
that names who speaks and says nothing about how they look -- `speaker_name` is a
legal optional key of the very same row, so it is the shape this invites -- and one
missing field killed a finished 14-shot plan.

The rule lives here rather than in `schema.py` for the reason every seam in this repo
does: that file is upstream's, its budget is measured, and a boundary policy belongs
in our own module with the upstream file keeping a one-line call.

Every planner's rows arrive through that one function (six call sites plus the plan
round-trip in `ShotPlan.from_dict`), so this is the whole blast radius.
"""

from __future__ import annotations

from typing import Any


def subject_ref_fields(row: Any) -> dict:
    """The `SubjectRef` fields of one row, with the description it owes.

    A row that omits the description is still a real participant, so it is described
    by whatever it does state -- the speaker's name, their character id -- and by
    "a person" as the last resort. The alternative is what happened: the render stops.
    """

    if not isinstance(row, dict):
        row = {"visual_description": str(row or "")}
    description = str(row.get("visual_description") or "").strip()
    if not description:
        description = str(
            row.get("speaker_name") or row.get("character_id") or "a person"
        ).strip()
    return {
        "visual_description": description,
        "character_id": row.get("character_id"),
        "position_or_relation": row.get("position_or_relation"),
        "wardrobe": row.get("wardrobe"),
        "speaker_name": row.get("speaker_name"),
        "performance_role": row.get("performance_role"),
    }
