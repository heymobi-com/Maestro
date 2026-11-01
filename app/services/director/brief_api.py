"""Routes for checking and restructuring a brief before it is planned.

Kept out of ``launch.py`` so it can be exercised through ASGI without booting the
whole application: the brief is the input every measured planning failure starts
from, so its check has to be testable on its own.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request

from .brief_assistant import (
    diagnose_brief,
    normalized_brief,
    strip_template_placeholders,
    structure_prompt,
)

__all__ = ["create_router", "strip_code_fences"]


def strip_code_fences(text: str) -> str:
    """The brief inside a ```text fence, if the model wrapped its answer in one."""

    if "```" not in text:
        return text
    parts = text.split("```")
    for part in parts:
        body = part
        if body.lstrip().lower().startswith(("text", "markdown", "md")):
            body = body.split("\n", 1)[-1]
        if re.search(r"(?i)\b(subject lock|characters|description)\b\s*:", body):
            return body
    return parts[1] if len(parts) > 1 else text


def _report_of(brief: str, body: dict) -> dict:
    return diagnose_brief(
        brief,
        kind=str(body.get("pipeline_type") or ""),
        clip_count=int(body.get("clip_count") or 0),
        transcript_speakers=body.get("transcript_speakers") or (),
        has_audio=bool(body.get("has_audio")),
    )


def create_router(*, generate=None) -> APIRouter:
    """Build the brief routes.

    ``generate`` is the LLM entry point (``(prompt, system_prompt, max_new_tokens)``).
    It is injected so the route can be tested without a model, and so a caller can
    supply a remote one unchanged.
    """

    router = APIRouter()

    @router.post("/api/v1/director/brief/review")
    async def review_brief(request: Request):
        """Check a brief against the shape this workflow's planner reads."""
        body = await request.json()
        return _report_of(str(body.get("brief") or ""), body)

    @router.post("/api/v1/director/brief/structure")
    async def structure_brief(request: Request):
        """Rewrite a brief into the canonical shape, then re-check it."""
        body = await request.json()
        brief = str(body.get("brief") or "")
        kind = str(body.get("pipeline_type") or "")
        clip_count = int(body.get("clip_count") or 0)
        speakers = body.get("transcript_speakers") or ()
        characters = body.get("characters") or ()

        before = _report_of(brief, body)
        mechanical = normalized_brief(brief, kind=kind)

        proposal = mechanical
        method = "normalized"
        if body.get("use_llm", True) is not False and generate is not None:
            system, user = structure_prompt(
                mechanical, kind=kind, diagnosis=before, clip_count=clip_count,
                transcript_speakers=speakers, characters=characters,
            )
            try:
                raw = generate(
                    user,
                    system,
                    max(1024, min(4096, len(mechanical) // 3 + 900)),
                )
                # The model copies instruction lines out of the prompt sometimes: a real
                # proposal opened with "<every other character>: named as presences…".
                candidate = strip_template_placeholders(
                    strip_code_fences(str(raw or "")).strip(),
                ).strip()
                # A rewrite that loses the cast or the story is worse than none: keep
                # the mechanical result unless the model actually produced a brief.
                if len(candidate) >= 200 and ":" in candidate:
                    proposal = candidate
                    method = "llm"
            except Exception as exc:  # never block the person on the model
                print(f"[Director] Brief structuring fell back to the mechanical pass: {exc}")

        after = _report_of(proposal, body)
        return {
            "brief": proposal,
            "method": method,
            "before": before,
            "after": after,
            "resolved": sorted(
                {finding["code"] for finding in before["findings"]}
                - {finding["code"] for finding in after["findings"]}
            ),
            "remaining": [finding["code"] for finding in after["findings"]],
        }

    return router
