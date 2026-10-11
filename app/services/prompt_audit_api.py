"""The prompt audit, where it can be read before a render is paid for.

The audit runs inside the pipeline before any model is loaded, and its findings used to go to the
server log: a director reading a terminal is not where the decision to spend GPU hours gets made.
The same audit is exposed here for a saved pipeline, so the interface can show it while the run is
still waiting, and so a script can check a project without opening the interface at all.

Nothing here changes a prompt. The audit reports; repairing stays the separate, opt-in step it was.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.director.prompt_audit import audit_project_prompts, format_audit_report

__all__ = ["router"]

router = APIRouter()


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
    audit = audit_project_prompts(clips)
    return {**audit, "pid": pid, "report": format_audit_report(audit, limit=12)}
