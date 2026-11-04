"""Routes for checking and restructuring a brief before it is planned.

Kept out of ``launch.py`` so it can be exercised through ASGI without booting the
whole application: the brief is the input every measured planning failure starts
from, so its check has to be testable on its own.
"""

from __future__ import annotations

import json
import os
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


def _outputs_root() -> str:
    """Where Director writes its projects, relative to ``app``."""

    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "outputs")


def _saved_references(pipeline_id: str) -> list:
    """The reference manifest a saved project carries, when the app sends none.

    The panel already sends the manifest it is showing, but a request can arrive without
    it — an older UI build, a script, a resumed project. Without references the assistant
    honestly reports "no picture reached this check" while the project plainly has three,
    which reads as a bug in the assistant. The project's own state file is the durable
    truth, so it is read here by id.
    """

    pid = str(pipeline_id or "").strip()
    if not pid:
        return []
    root = _outputs_root()
    if not os.path.isdir(root):
        return []
    matches: list[str] = []
    for workspace in os.listdir(root):
        candidate = os.path.join(root, workspace, f"_director_pipeline_{pid}.json")
        if os.path.isfile(candidate):
            matches.append(candidate)
    if not matches:
        return []
    try:
        with open(max(matches, key=os.path.getmtime), encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception as exc:  # noqa: BLE001 - a fallback may fail quietly
        print(f"[Director] Could not read the saved manifest for {pid}: {exc}")
        return []
    snapshot = state.get("_params_snapshot") or {}
    references = snapshot.get("minimax_h3_references") or state.get("minimax_h3_references") or []
    references = [item for item in references if isinstance(item, dict)]
    if references:
        print(
            f"[Director] Brief check used the manifest saved with project {pid} "
            f"({len(references)} reference(s))."
        )
    return references


def _references_of(body: dict) -> list:
    """The references this request knows about: the panel's, or the project's own."""

    supplied = body.get("references") or []
    if supplied:
        return [item for item in supplied if isinstance(item, dict)]
    return _saved_references(str(body.get("pipeline_id") or body.get("project_id") or ""))


def _report_of(brief: str, body: dict) -> dict:
    return diagnose_brief(
        brief,
        kind=str(body.get("pipeline_type") or ""),
        clip_count=int(body.get("clip_count") or 0),
        transcript_speakers=body.get("transcript_speakers") or (),
        has_audio=bool(body.get("has_audio")),
        references=_references_of(body),
    )


def _cast_looks(body: dict, generate=None) -> dict:
    """Appearances read from the identity references the project already carries.

    The writer should not have to describe a face that is in a picture. The look is
    read once per name and then used to fill CHARACTERS, so the brief and the image
    say the same thing.
    """

    from .cast_vision import cast_descriptions

    return cast_descriptions(_references_of(body), generate=generate)


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

    @router.post("/api/v1/director/brief/cast")
    async def read_cast(request: Request):
        """Read who each identity reference shows, with the vision model.

        Returned on its own so the panel can show the sentences before anything is
        written into the brief, and so a caller can reuse them.
        """
        body = await request.json()
        looks = _cast_looks(body, generate=generate)
        from .cast_vision import vision_status

        return {
            "looks": [{"name": name, "look": look} for name, look in looks.items()],
            "count": len(looks),
            "available": bool(looks),
            "references": len(_references_of(body)),
            "vision": vision_status(),
        }

    @router.post("/api/v1/director/brief/structure")
    async def structure_brief(request: Request):
        """Rewrite a brief into the canonical shape, then re-check it."""
        body = await request.json()
        brief = str(body.get("brief") or "")
        kind = str(body.get("pipeline_type") or "")
        clip_count = int(body.get("clip_count") or 0)
        speakers = body.get("transcript_speakers") or ()
        characters = body.get("characters") or ()
        references = _references_of(body)

        from .cast_vision import (
            apply_cast_descriptions,
            apply_reference_roster,
            reference_roster,
        )

        looks = _cast_looks(body, generate=generate)
        roster = reference_roster(references, looks)

        def ground(text: str) -> str:
            """Everything the manifest knows, written into the text, mechanically."""
            from .brief_assistant import (
                consolidate_cast_rows,
                drop_repeated_sections,
                normalize_speaker_cues,
            )

            grounded = normalize_speaker_cues(text, speakers)
            grounded = apply_cast_descriptions(grounded, looks) if looks else grounded
            if roster:
                grounded = apply_reference_roster(grounded, references, looks)
            return drop_repeated_sections(consolidate_cast_rows(grounded))

        before = _report_of(brief, body)
        # The cue, the roster and the picture's own sentences go in before the model
        # reads the text, so what it reorganises already names the speaker and holds the
        # face instead of describing a person it has never seen.
        mechanical = ground(
            normalized_brief(
                brief, kind=kind, transcript_speakers=speakers, references=references,
            )
        )

        proposal = mechanical
        method = "normalized"
        replaced: list[str] = []
        if body.get("use_llm", True) is not False and generate is not None:
            system, user = structure_prompt(
                mechanical, kind=kind, diagnosis=before, clip_count=clip_count,
                transcript_speakers=speakers, characters=characters,
                has_audio=bool(body.get("has_audio") or speakers),
                roster=roster,
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
                    # The model may repair the machine-readable half -- the lock, the rows
                    # that bind a name to a voice and to its picture -- and nothing else.
                    # It used to replace the whole brief, so pressing Structure handed back
                    # a different film and the plan rendered that one instead.
                    from .brief_assistant import merge_structured

                    # Compared against the person's own text, not against the mechanical
                    # draft: a freeform brief has no sections, so the model's structure is
                    # the structure, while a written DESCRIPTION is theirs to keep.
                    merged, replaced = merge_structured(brief, ground(candidate))
                    proposal = ground(merged) if merged.strip() else mechanical
                    method = "llm"
                    if replaced:
                        print(
                            "[Director] Structure kept your own "
                            f"{', '.join(sorted(replaced))}: the proposal rewrote it."
                        )
            except Exception as exc:  # never block the person on the model
                print(f"[Director] Brief structuring fell back to the mechanical pass: {exc}")

        after = _report_of(proposal, body)
        from .cast_vision import vision_status

        return {
            "brief": proposal,
            "method": method,
            "looks": [{"name": name, "look": look} for name, look in looks.items()],
            "roster": roster,
            "speakers": after.get("speakers") or [],
            "speaker_map": after.get("speaker_map") or {},
            "vision": vision_status(),
            "references": len(references),
            "before": before,
            "after": after,
            "resolved": sorted(
                {finding["code"] for finding in before["findings"]}
                - {finding["code"] for finding in after["findings"]}
            ),
            "remaining": [finding["code"] for finding in after["findings"]],
            "kept": sorted(replaced) if method == "llm" else [],
        }

    return router
