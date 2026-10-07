"""The plan a clip is given when its planning batch did not come back.

This is the ``fallback_factory`` the long-form music-video planner hands to
``BasePlanner._run_checkpointed_json_batches``: one row per clip that the model's answer did
not cover, so the timeline keeps its clip count and the finished film has a shot in that slot.

**Measured harm, and why this lives in its own file.** The placeholder used to be an inline
lambda in the planner whose ``video_prompt`` was ``scene_description`` -- the project's own
briefing text. On a real 30-shot project one failed batch left **10 of 30 clips carrying the
identical 3050-character prompt**, which was that briefing word for word. Those clips rendered
the film's instructions as if they were a shot: the same content repeating, no per-shot design,
and no way to continue from a neighbour. The same rows also arrive with every design field
empty, which is how the loss shows up in a coverage audit.

So the placeholder is now built here, from the clip's own place in the song, and it never
carries the film's text. It is still a placeholder -- ``scene_goal`` says what it is and the
caller reports how many clips were filled this way -- but a clip given one can no longer be
mistaken for the briefing, and repairing that clip means planning it again, not guessing.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["shot_placeholder"]


def shot_placeholder(index: int, clip: Any) -> dict:
    """One honest, self-contained generic shot, for a clip whose batch did not answer."""

    source = clip if isinstance(clip, Mapping) else {}
    section = str(source.get("label") or "music").strip() or "music"
    return {
        "scene_goal": f"Continue the {section} section at global clip {index + 1}",
        "scene_type": (
            "atmospheric" if section.lower() == "instrumental" else "performance"
        ),
        "subjects_on_screen": [],
        "environment": "",
        "visual_style": "",
        "lighting": "",
        "mood": "",
        "action_beats": [],
        "camera_plan": {"framing": "medium shot"},
        "ending_beat": "The performance continues into the next clip",
        # Never the film's own text: a placeholder that quotes the briefing renders the
        # briefing. It has to be renderable on its own, and short, because it describes a
        # consequence of a failure rather than a decision.
        "video_prompt": (
            "One continuous shot in the project's established setting and wardrobe, "
            f"carrying the {section} section forward with the same lighting and camera "
            "language as the surrounding clips."
        ),
        "window_prompts": [],
    }
