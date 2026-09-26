"""The parts of a timeline-driven viral plan that are ours, kept out of upstream's file.

``viral_video.py`` is a file upstream also owns, and every line of ours inside it is
a line that can conflict at the next release. The text and the data below are all
ours -- the soundtrack note, the long-form batch contract, the whole-timeline
overview and the deterministic fallback shot -- so they live here and the planner
imports them. ``scripts/upstream_update_guard.py`` measures that surface, and the
extraction is what keeps the ratchet green while the planner grows.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def soundtrack_note(has_soundtrack: bool) -> str:
    """What the model must know when the user supplied the audio.

    The plan used to say ``generated_audio`` for every shot, which asks the model to
    invent its own audio instead of following the track the user supplied: that is
    what made characters sing or lip-sync to the music they were supposed to be
    working over.
    """

    if not has_soundtrack:
        return ""
    return (
        "The uploaded soundtrack drives the timing and it is finished audio: the "
        "characters do not perform it. Do not write singing, do not move mouths to "
        "the music, and keep lip movement to a tagged spoken line only. Its "
        "transcription is context for the story beats, not words to re-enact."
    )


def timeline_overview(clips: Sequence[Mapping[str, Any]]) -> str:
    """One line per clip, so a batch can see where it sits and what is still ahead."""

    return "\n".join(
        f"Clip {index + 1}: {float(clip.get('start', 0) or 0):.1f}-"
        f"{float(clip.get('end', 0) or 0):.1f}s"
        for index, clip in enumerate(clips)
    )


def batch_seconds(clips: Sequence[Mapping[str, Any]]) -> float:
    """The span a batch covers, with a stated placeholder when timings are unwritten."""

    if not clips:
        return 0.0
    seconds = max(
        (float(clip.get("end", 0) or 0) for clip in clips), default=0.0,
    ) - min((float(clip.get("start", 0) or 0) for clip in clips), default=0.0)
    if seconds <= 0:
        # A saved timeline can arrive with its timings unwritten; the shot count
        # still comes from the clips, which is what matters here.
        seconds = len(clips) * 5.0
    return seconds


def batch_note(
    batch_number: int,
    start: int,
    end: int,
    total: int,
    previous_ending: str,
    overview: str,
    speakers: str = "",
) -> str:
    """The long-form contract a batch is planned under.

    Every batch receives the whole timeline as context and is told which shots are
    its own, because the previous version seeded each batch only with the previous
    ending and a long plan degraded from the middle on.
    """

    return (
        "LONG-FORM TIMELINE CONTRACT:\n"
        f"This is planning batch {batch_number}, covering global shots "
        f"{start + 1}-{end} of {total}. Continue the same video; do "
        "not restart its premise or repeat completed shot ideas. The "
        "soundtrack is already fixed and its transcription is immutable.\n"
        f"Previous planned ending: "
        f"{previous_ending or 'No prior shot; establish the opening.'}\n\n"
        "THE WHOLE TIMELINE (context only: see where this batch sits and "
        f"what is still ahead; plan ONLY shots {start + 1}-{end}):\n"
        f"{overview}"
        f"{speakers}"
    )


def fallback_shot(index: int, clip: Mapping[str, Any]) -> dict:
    """A batch that did not come back still yields one shot per clip, said out loud."""

    return {
        "scene_goal": f"Continue the video at global shot {index + 1}",
        "scene_type": "escalation",
        "duration_sec": max(
            1.0,
            float(clip.get("end", 0) or 0) - float(clip.get("start", 0) or 0),
        ),
        "subjects_on_screen": [],
        "environment": "",
        "visual_style": "",
        "lighting": "",
        "mood": "",
        "action_beats": [],
        "dialogue_beats": [],
        "camera_plan": {},
        "audio_plan": {"mode": "generated_audio"},
        "ending_beat": "",
    }
