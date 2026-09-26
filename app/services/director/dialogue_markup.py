"""Normalise a planner-authored line at the boundary it crosses.

This used to live inside ``schema.DialogueBeat.from_dict``. It moved here for the
same reason as the rest of our seams: ``schema.py`` is a file upstream also owns,
and the boundary needs room for its own guards.

The compiler owns the rule (see ``h3_dialogue.strip_dialogue_markup``); this module
only makes it unavoidable, so the saved plan and the review UI show words instead of
each consumer having to remember to clean them. The compiler is imported lazily
because it is heavy and does not depend on this module.
"""

from __future__ import annotations

from typing import Any


def normalize_spoken_text(value: Any) -> str:
    """The spoken words of one beat, with its H3 wrapper and label removed."""

    spoken = str(value or "").strip()
    if not spoken:
        return spoken
    from .h3_dialogue import strip_dialogue_markup
    return strip_dialogue_markup(spoken)
