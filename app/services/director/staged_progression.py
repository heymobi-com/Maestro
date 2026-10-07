"""The staged-progression contract, attached to every planner's system prompt.

A process with stages -- a plant sprouting, blooming and drying; a storm arriving and
clearing -- is ONE story beat spread over several consecutive shots. Measured on a real
30-clip project, the complete plant process appeared with all six of its terms inside EACH
of clips 8 to 15, which lasted two to nine seconds each, and again at 23 and 29; rain or
lightning appeared in 26 of the 30 shots. The effects therefore flashed past and repeated
instead of advancing, which is the reported "no hilacion de escena entre un clip y otro".

Upstream's guides never state this, and the LTX music-video guide bans continuity wording
outright, so the contract lives in a guide of ours and is attached where every planner's
system prompt passes: ``BasePlanner._call_llm_json``. That is the only place that covers all
five workflows -- video musical, short film (audio and story), podcast and viral video --
because podcast and viral load no guide at all and pass no JSON schema to filter on.

Upstream's own files stay byte-identical: the text lives in
``llm_guides/director/maestro_staged_progression_rules.md``, and the one hook in
``planners/base.py`` is paid for by compacting our own comments in that file.
"""

from __future__ import annotations

import os
from typing import Any

from services.director.schema import VALID_SKILL_TYPES

__all__ = ["with_shot_progression"]

_GUIDES_DIR = os.path.join(os.path.dirname(__file__), "..", "llm_guides", "director")
_ADDENDUM = "maestro_staged_progression_rules.md"

# The first line of the addendum, used to keep the contract out of a prompt that already
# carries it: the JSON-fix retry reuses the same system prompt a second time.
_MARKER = "STAGED PROGRESSION"


def with_shot_progression(system_prompt: Any, skill_type: Any = "") -> str:
    """``system_prompt`` with the staged-progression contract, for a shot planner.

    Only a planner that plans a film's shots gets it. ``BasePlanner`` is also used directly
    by tests and small callers whose system prompt is not planning shots, and they have no
    use for shot-staging rules. The set is the schema's own, so a new skill is covered by
    adding it there and nowhere else.

    A missing addendum never breaks planning: this is guidance, and the call it is attached
    to is the one that plans shots.
    """

    content = str(system_prompt or "")
    if str(skill_type or "") not in VALID_SKILL_TYPES:
        return content
    if _MARKER in content:
        return content
    filepath = os.path.join(_GUIDES_DIR, _ADDENDUM)
    if not os.path.isfile(filepath):
        print(f"[GuideLoader] Guide not found: {_ADDENDUM}")
        return content
    try:
        with open(filepath, "r", encoding="utf-8") as handle:
            addendum = handle.read().strip()
    except Exception as exc:  # noqa: BLE001 - guidance must never abort a plan
        print(f"[GuideLoader] Failed to load {_ADDENDUM}: {exc}")
        return content
    if not addendum:
        return content
    return f"{content}\n\n{addendum}" if content.strip() else addendum
