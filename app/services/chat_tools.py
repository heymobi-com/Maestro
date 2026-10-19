"""What the assistant may read, and what it may propose.

The assistant is a guest in a project that is already planned. Everything here is therefore
read-only except two proposals, and a proposal is not an edit: the prompt is written into the
answer for the user to look at, checked against the same contract the correction panel uses, and
applied only when they say so. That is the rule the prompt audit already states -- "nothing is
blocked and nothing is rewritten behind the director's back: the finding is signalled, the
correction is offered, and the write happens when they press save" -- and a chat with a hosted
model as its brain has no business being the exception.

The validator is reused rather than rewritten: `review_h3_revision` is what refuses a rewrite
that drops a spoken line, writes a second shot into a one-shot body, or leaves the prompt
without its contract, and it is already covered by the correction panel's tests. A second copy
of those rules would drift from the first.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

__all__ = ["READ_TOOLS", "PROPOSE_TOOLS", "read_tool", "propose_tool", "tools_hint_lines"]

READ_TOOLS = frozenset({
    "get_pipeline_state",
    "get_clip_prompt",
    "get_prompt_audit",
    "get_recent_logs",
    "get_project_text",
})
PROPOSE_TOOLS = frozenset({"propose_clip_prompt", "propose_clip_tag"})

_LOG_TAIL_DEFAULT = 120
_LOG_TAIL_MAX = 400


def _shot_number(action: Mapping[str, Any]) -> Optional[int]:
    try:
        return int(action.get("shot"))
    except (TypeError, ValueError):
        return None


def _clip(state: Mapping[str, Any], action: Mapping[str, Any]) -> Optional[dict]:
    """The shot an action names, as the saved plan holds it."""

    index = _shot_number(action)
    if index is None:
        return None
    clips = [clip for clip in (state.get("clips") or []) if isinstance(clip, dict)]
    for position, clip in enumerate(clips, start=1):
        if int(clip.get("index", position - 1)) + 1 == index:
            return clip
    return None


def _state_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """The plan, reduced to what a question about it actually needs."""

    clips = [clip for clip in (state.get("clips") or []) if isinstance(clip, dict)]
    shots = []
    for position, clip in enumerate(clips, start=1):
        planned = clip.get("planned_clip") or {}
        shots.append({
            "shot": int(clip.get("index", position - 1)) + 1,
            "group": planned.get("_director_continuity_group"),
            "strategy": planned.get("_director_continuity_strategy"),
            "seconds": round(float(clip.get("_director_duration_sec") or 0), 2),
            "rendered": bool(clip.get("video_filename")),
            "tag": clip.get("tag"),
        })
    return {
        "pipeline_id": state.get("pipeline_id"),
        "status": state.get("status"),
        "phase": state.get("phase"),
        "video_model": state.get("video_model"),
        "shot_count": len(clips),
        "shots": shots,
    }


def _read_pipeline(workspace: str, action: Mapping[str, Any]) -> Any:
    """The saved state a question is about: the named pipeline, or the newest one."""

    from services.director_pipeline import list_pipeline_states, load_pipeline_state

    base = os.path.join("outputs", workspace) if workspace else "outputs"
    pid = str(action.get("pipeline_id") or "").strip()
    if not pid:
        listed = list_pipeline_states(base)
        if not listed:
            return {"error": f"No saved Director project in '{workspace or 'outputs'}'."}
        pid = str(listed[0].get("pipeline_id") or listed[0].get("id") or "")
    if not pid:
        return {"error": "The newest saved project has no id to read."}
    state = load_pipeline_state(base, pid)
    if not state:
        return {"error": f"No saved plan for pipeline {pid}."}
    return state


def read_tool(name: str, action: Mapping[str, Any], *, workspace: str = "") -> dict[str, Any]:
    """Run one read-only tool and return what the model should see.

    Every result is a small dictionary rather than a dump: a light model's window is the scarce
    resource here, and handing it a whole plan or a whole log is how a useful answer turns into
    a truncated one.
    """

    if name not in READ_TOOLS:
        return {"error": f"'{name}' is not a tool the assistant may run."}

    if name == "get_recent_logs":
        try:
            wanted = int(action.get("lines") or _LOG_TAIL_DEFAULT)
        except (TypeError, ValueError):
            wanted = _LOG_TAIL_DEFAULT
        wanted = max(20, min(_LOG_TAIL_MAX, wanted))
        path = os.path.join("logs", "api", "start.js", "latest")
        if not os.path.isfile(path):
            return {"error": "There is no render log on disk yet."}
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            tail = handle.read().splitlines()[-wanted:]
        return {"log": "logs/api/start.js/latest", "lines": len(tail), "tail": "\n".join(tail)}

    try:
        state = _read_pipeline(workspace, action)
    except Exception as error:  # a missing folder or an unreadable plan is an answer, not a crash
        return {"error": f"The project could not be read: {error}"}
    if isinstance(state, dict) and state.get("error"):
        return state

    if name == "get_pipeline_state":
        return _state_summary(state)

    if name == "get_project_text":
        for clip in state.get("clips") or []:
            context = str((clip or {}).get("_director_project_context") or "")
            if context:
                return {"project_text": context}
        return {"error": "This project carries no shared brief."}

    if name == "get_clip_prompt":
        clip = _clip(state, action)
        if clip is None:
            return {"error": f"The plan has no shot {action.get('shot')}."}
        return {
            "shot": _shot_number(action),
            "prompt": clip.get("video_prompt") or "",
            "draft": clip.get("_director_h3_source_prompt") or "",
        }

    if name == "get_prompt_audit":
        from services.director.prompt_audit import audit_project_prompts, format_audit_report

        audit = audit_project_prompts(
            state.get("clips") or [],
            project_context=str(state.get("scene_description") or ""),
        )
        return {"totals": audit.get("totals") or {}, "report": format_audit_report(audit)}

    return {"error": f"'{name}' has no reader."}


def propose_tool(
    name: str,
    action: Mapping[str, Any],
    *,
    workspace: str = "",
) -> dict[str, Any]:
    """Check one proposed edit and hand it back for someone to accept.

    Nothing is written. The answer says whether the proposal would be accepted, in the
    validator's own words when it would not, so the model can try again inside the same turn
    instead of the user discovering the problem after pressing apply.
    """

    if name not in PROPOSE_TOOLS:
        return {"error": f"'{name}' is not a tool the assistant may propose."}
    if name == "propose_clip_tag":
        tag = str(action.get("tag") or "").strip()
        shot = _shot_number(action)
        if shot is None or not tag:
            return {"error": "A tag needs a shot number and a tag."}
        return {"kind": "tag", "shot": shot, "tag": tag[:80], "acceptable": True, "problems": []}

    shot = _shot_number(action)
    proposed = str(action.get("prompt") or "")
    if shot is None or not proposed.strip():
        return {"error": "A prompt proposal needs a shot number and the whole new prompt."}

    from services.director.h3_dialogue import _h3_plan_context_anchors, review_h3_revision

    try:
        state = _read_pipeline(workspace, action)
    except Exception as error:
        return {"error": f"The project could not be read: {error}"}
    if isinstance(state, dict) and state.get("error"):
        return state
    clip = _clip(state, action)
    if clip is None:
        return {"error": f"The plan has no shot {shot}."}

    current = str(clip.get("video_prompt") or "")
    # The same frozen list the correction panel builds: the project's shared text is what a model
    # damages when it re-types a prompt, so no single shot may edit it and the gate is told so.
    from services.director.h3_dialogue import h3_shared_project_phrases

    frozen = [
        text for text in [str(clip.get("_director_project_context") or "").strip()] if text
    ] + h3_shared_project_phrases(
        [str(item.get("video_prompt") or "") for item in (state.get("clips") or [])]
    )
    problems = review_h3_revision(
        current,
        proposed,
        duration_seconds=clip.get("_director_duration_sec") or 0.0,
        subjects=clip.get("_director_subjects_on_screen") or [],
        mode=clip.get("_director_h3_prompt_mode") or "ref2va",
        references=clip.get("_director_h3_reference_manifest") or [],
        context_anchors=_h3_plan_context_anchors(clip),
        frozen=frozen,
    )
    return {
        "kind": "prompt",
        "shot": shot,
        "prompt": proposed,
        "current": current,
        "acceptable": not problems,
        "problems": list(problems),
    }


def tools_hint_lines() -> Sequence[str]:
    """The tool names, for a caller that wants to state the contract without the prose."""

    return tuple(sorted(READ_TOOLS)) + tuple(sorted(PROPOSE_TOOLS))
