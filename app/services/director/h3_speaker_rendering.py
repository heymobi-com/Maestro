"""Spoken lines the H3 renderer will refuse, found before the model is loaded.

The renderer reads the character of a line from a name or an explicit ``<Subject N>`` tag beside
it and raises rather than guess a voice. That check ran only inside the model, so a shot whose cue
named nobody loaded 20 GB of weights and then failed with the prompt still unchanged. The same
condition is measured here instead, and the measurement stays out of ``director_pipeline`` because
upstream executes that module's preflight function on its own in tests, where a reference to a
sibling private helper is a NameError.
"""

from __future__ import annotations

import re

__all__ = ["unrenderable_speaker_lines"]

# Distinct subject/speaker identities a compiled prompt declares. The renderer only has to pick a
# voice when there is more than one; with a single character it resolves implicitly.
_SUBJECT_ID_RE = re.compile(r"[(\[](S\d+)[)\]]|<Subject\s*(\d+)>", re.IGNORECASE)


def unrenderable_speaker_lines(clip_plans: list[dict]) -> list[str]:
    """The spoken lines of ``clip_plans`` that no cue attributes to a character."""

    from services.director.h3_dialogue import h3_unresolved_speaker_cue_problems

    problems: list[str] = []
    for index, plan in enumerate(clip_plans):
        prompt = str(plan.get("video_prompt") or "")
        if not prompt:
            continue
        identities = {
            # ``(S1)`` and ``<Subject 1>`` name the same character, so both forms are read as
            # one identity rather than two.
            int((letter_id or number_id).lstrip("Ss"))
            for letter_id, number_id in _SUBJECT_ID_RE.findall(prompt)
        }
        if len(identities) < 2:
            continue
        for problem in h3_unresolved_speaker_cue_problems(prompt):
            problems.append(f"shot {index + 1}: {problem}")
    return problems
