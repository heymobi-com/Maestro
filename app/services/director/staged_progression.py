"""The staged-progression contract, attached to the guides that write shots.

A process with stages -- a plant sprouting, blooming and drying; a storm arriving and
clearing -- is ONE story beat spread over several consecutive shots. Measured on a real
30-clip project, the complete plant process appeared with all six of its terms inside EACH
of clips 8 to 15, which lasted two to nine seconds each, and again at 23 and 29; rain or
lightning appeared in 26 of the 30 shots. The effects therefore flashed past and repeated
instead of advancing, which is the reported "no hilacion de escena entre un clip y otro".

Upstream's guides never state this, and the LTX music-video guide bans continuity wording
outright, so the contract lives in a guide of ours and is appended to the shot-writing
guides by the loader. Upstream's own guide files stay byte-identical on disk, so an update
can never conflict inside a guide.

The loader is the only hook needed, and it is the safest one available: measured on
upstream's own history, ``guide_loader.py`` has been touched by one commit, while
``planners/music_video.py`` -- the other place this text could go -- has eight against it.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["with_shot_progression"]

_GUIDES_DIR = os.path.join(os.path.dirname(__file__), "..", "llm_guides", "director")

# The guides that decide how the shots of a film are written.
_SHOT_WRITING_GUIDES = frozenset({
    "minimax_h3_shot_breakdown.md",
    "ltx2_music_video_rules.md",
})

_ADDENDUM = "maestro_staged_progression_rules.md"


def with_shot_progression(content: str, filename: Any) -> str:
    """``content`` with the staged-progression contract, for the shot-writing guides.

    A guide that does not write shots comes back untouched, and a missing addendum never
    breaks planning: it is guidance, not a contract the compiler enforces.
    """

    if filename not in _SHOT_WRITING_GUIDES:
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
    return f"{content}\n\n{addendum}" if addendum else content
