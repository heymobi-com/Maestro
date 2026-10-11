"""The prompt audit, where it can be read before a render is paid for.

The audit runs inside the pipeline before any model is loaded, and its findings used to go to the
server log: a director reading a terminal is not where the decision to spend GPU hours gets made.
The same audit is exposed here for a saved pipeline, so the interface can show it while the run is
still waiting, and so a script can check a project without opening the interface at all.

Nothing here changes a prompt. The audit reports; repairing stays the separate, opt-in step it was.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import APIRouter, HTTPException

from services.director.prompt_audit import audit_project_prompts, format_audit_report
from services.director.prompt_contradictions import contradiction_findings, suggested_fix

__all__ = ["annotated", "router"]

router = APIRouter()


def annotated(audit: dict, clips: Sequence[Any]) -> dict:
    """The audit with, for each shot, what the finding is about and what can be done about it.

    A finding carries the prompt it came from, whether that prompt is where it lives (one code is
    also checked in the pre-polish draft, and editing the compiled prompt would clear nothing), and
    the corrected text when the contradiction is a leftover piece of text rather than two
    descriptions disagreeing. Nothing here is applied: the panel shows it and the director decides.
    """

    prompts = {position: str((clip or {}).get("video_prompt") or "") for position, clip in enumerate(clips)}
    drafts = {
        position: str((clip or {}).get("_director_h3_source_prompt") or "")
        for position, clip in enumerate(clips)
    }
    for shot in audit.get("findings") or []:
        position = int(shot.get("index", 0))
        number = int(shot.get("shot", position + 1))
        text = prompts.get(position, "")
        draft = drafts.get(position, "")
        in_prompt = {
            finding["code"] for finding in contradiction_findings(text, shot=number)
        }
        shot["prompt"] = text
        for finding in shot.get("findings") or []:
            lives_here = finding["code"] in in_prompt
            finding["in_prompt"] = lives_here
            finding["fix"] = (
                suggested_fix(text, finding["code"], draft=draft, shot=number) if lives_here else ""
            )
    return audit


@router.get("/api/v1/director/pipelines/{pid}/prompt-audit")
def pipeline_prompt_audit(pid: str):
    """Every contract fault and contradiction in one saved pipeline's compiled prompts.

    The plans a run was built from are already on disk, so this needs no GPU and no live pipeline:
    it answers for a finished project as well as for one that is about to be rendered.
    """

    import wgp

    from services.director_pipeline import load_pipeline_state

    state = load_pipeline_state(wgp.server_config.get("save_path", "outputs"), pid)
    if not state:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    clips = [clip for clip in (state.get("clips") or []) if isinstance(clip, dict)]
    audit = annotated(audit_project_prompts(clips), clips)
    return {**audit, "pid": pid, "report": format_audit_report(audit, limit=12)}
