"""The correction conversation as its own route, so `launch.py` pays two lines for it.

The endpoint body has to sit inside the app that owns `wgp` and the event loop, which is why it
was written in `app/launch.py` -- a file upstream owns. Registering a router here instead keeps
the route, its reading and its refusals in a module only this fork has, the way `chat_api` and
`prompt_audit_api` already do: upstream's file pays the import and the `include_router`, and a
merge cannot conflict with a body it does not contain.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/v1/director/pipelines/{pid}/clips/{clip_index}/revise-prompt")
async def revise_pipeline_clip_prompt(pid: str, clip_index: int, request: Request):
    """One turn of the correction conversation for a single shot.

    The director says what is wrong in plain language; the assistant reports what
    in the prompt causes it, asks when the note is missing an intent, and returns
    a corrected prompt that only reaches the UI if it keeps every spoken line,
    carries exactly one shot and passes the prompt contract. The measurement, the
    adjacent shots and the turns so far travel with the note so the rewrite stays
    continuous with the rest of the sequence.
    """

    import wgp

    from services.director_pipeline import revise_clip_prompt
    from services.director_prompt_revision import revision_request_payload

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
    turn, refusal = revision_request_payload(body)
    if turn is None:
        return JSONResponse({"error": refusal}, status_code=400)
    base = wgp.server_config.get("save_path", "outputs")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: revise_clip_prompt(
                base, pid, clip_index, turn["instruction"], turn["prompt"],
                turn["history"],
            ),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # LLM/backend failure: report it, never a silent no-op
        return JSONResponse({"error": str(exc)}, status_code=500)
    return result
