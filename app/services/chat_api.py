"""The assistant's HTTP surface.

Three routes, and each answers a question the interface actually asks:

- ``POST /api/v1/assistant/message`` -- one turn. It runs the bounded tool loop and answers with
  the prose, whatever it read, and any edit it wants to propose. Nothing is written.
- ``POST /api/v1/assistant/apply`` -- the user accepted a proposal. This is the only route that
  writes, and it writes through the clip-prompt endpoint the Director's own editor uses, so an
  edit made from the chat and an edit made from the clip card are the same edit.
- ``GET /api/v1/assistant/settings`` -- what the assistant will actually use, and whether it
  routes off this machine. A chat is the one place a person needs to know that before typing.

It lives in its own module because `api/launch.py` belongs to upstream: the route file is ours,
so the assistant costs that file one registration line rather than a few hundred.
"""

from __future__ import annotations

import threading
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request

from services import chat_llm, chat_tools

router = APIRouter()

# Serialises the preferences file, the way the Services panel's own save does.
_SETTINGS_WRITE_LOCK = threading.RLock()

# Two rounds are enough for the shapes this answers -- read one thing, then read what it points
# at -- and a third would only be paid for by every question that needs none. It is a bound
# rather than a target: a model that asks for nothing gets one call.
MAX_TOOL_ROUNDS = 2

SYSTEM_PROMPT = (
    "You are Maestro's assistant, inside a local AI video studio. You help the person at the "
    "machine understand and improve the project they are working on: a Director project, a "
    "Studio render, the model settings, or a failure they are reading in a log.\n"
    "Answer in the language they write in. Be concrete and brief: name the shot, the field and "
    "the number. When you are unsure, say which file or state would settle it instead of "
    "guessing -- a wrong guess here costs GPU hours. Never claim to have changed anything: you "
    "propose, and the user applies.\n\n"
    + chat_llm.ACTIONS_HINT
)


def _services() -> Mapping[str, Any]:
    import wgp

    return wgp.server_config.get("services", {}) or {}


def _workspace(body: Mapping[str, Any]) -> str:
    """The workspace a question is about, defaulting to whatever is active."""

    named = str(body.get("workspace") or "").strip()
    if named:
        return named
    try:
        import wgp

        return str(wgp.server_config.get("active_workspace") or "").strip()
    except Exception:
        return ""


def _context_line(body: Mapping[str, Any]) -> str:
    """Where the person is standing, so an answer can be about *this* screen.

    Sent by the interface, which is the only part that knows which panel is open. It is a hint
    rather than a fact: the assistant is told what is on screen and reads the state itself when
    the answer depends on it.
    """

    area = str(body.get("area") or "").strip()
    workspace = _workspace(body)
    facts = []
    if area:
        facts.append(f"the user is in the {area} area")
    if workspace:
        facts.append(f"the open project is '{workspace}'")
    pipeline = str(body.get("pipeline_id") or "").strip()
    if pipeline:
        facts.append(f"pipeline {pipeline}")
    shot = body.get("shot")
    if isinstance(shot, int):
        facts.append(f"shot {shot}")
    if not facts:
        return ""
    return "[Screen context] " + "; ".join(facts) + "."


def _run_loop(services: Mapping[str, Any], messages: list[dict[str, Any]], body: Mapping[str, Any]):
    """Ask, run what it read, ask again, and stop. Returns (prose, reads, proposals)."""

    workspace = _workspace(body)
    reads: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    conversation = list(messages)
    for round_index in range(MAX_TOOL_ROUNDS + 1):
        reply = chat_llm.ask(
            services,
            conversation,
            images=list(body.get("images") or []) if round_index == 0 else [],
            system=SYSTEM_PROMPT,
        )
        prose, actions = chat_llm.parse_actions(reply)
        if not actions:
            return prose, reads, proposals
        asked_reads = [
            action for action in actions if str(action.get("tool")) in chat_tools.READ_TOOLS
        ]
        for action in actions:
            name = str(action.get("tool") or "")
            if name in chat_tools.PROPOSE_TOOLS:
                proposals.append(chat_tools.propose_tool(name, action, workspace=workspace))
        if not asked_reads:
            return prose, reads, proposals
        if round_index == MAX_TOOL_ROUNDS:
            return prose, reads, proposals
        results = []
        for action in asked_reads:
            result = chat_tools.read_tool(str(action.get("tool")), action, workspace=workspace)
            reads.append({"tool": action.get("tool"), "arguments": action, "result": result})
            results.append({"tool": action.get("tool"), "result": result})
        conversation = [
            *conversation,
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "Results of the tools you asked for:\n"
                + _json(results)
                + "\nAnswer the question now, using them.",
            },
        ]
    return "", reads, proposals


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=1)[:12000]


@router.get("/api/v1/assistant/settings")
def assistant_settings():
    """What the assistant will use, and what its own Settings block needs to prefill itself.

    The raw credential is never returned -- only whether one is stored and its mask, which is
    the same contract the Services panel keeps for every other key. Everything here is the
    assistant's own, so the panel does not have to be taught about it by the upstream route.
    """

    from services.llm_privacy import routes_off_machine

    services = _services()
    endpoint = chat_llm.chat_endpoint(services)
    follows_pipeline = not endpoint
    url = str(endpoint.get("base_url") or services.get("llm_remote_url") or "")
    key = str(services.get("chat_llm_api_key") or "")
    # The pipeline's model is the answer only while the pipeline is what answers. An assistant
    # pointed at another machine has no model until one is chosen for it, and reporting the
    # pipeline's here is how "In force: remote" came to name a model the chosen server never
    # served -- the setting looked ignored when it had simply never been filled in.
    model = str(endpoint.get("model") or "").strip()
    if not model and follows_pipeline:
        model = str(services.get("llm_model_id") or "").strip()
    return {
        "provider": chat_llm.chat_provider(services) or (services.get("llm_provider") or "local"),
        "follows_pipeline": follows_pipeline,
        # A chat answered by the engine the render uses must say so before anyone types: every
        # turn occupies it, so a question waits behind the render and the render behind the
        # question -- and measured on a local engine that is the difference in tok/s.
        "shares_pipeline_writer": chat_llm.shares_the_pipeline_writer(services, endpoint),
        "model": model,
        "needs_model": bool(endpoint) and not model,
        "endpoint": url,
        "vision": chat_llm.vision_capable(endpoint, services),
        "routes_off_machine": routes_off_machine(
            endpoint.get("provider") or services.get("llm_provider") or "local", url,
        ),
        "max_tokens": chat_llm.max_tokens(services),
        # The shape the Settings form edits, exactly as it is stored.
        "configured_provider": str(services.get("chat_llm_provider") or ""),
        "configured_model": str(services.get("chat_llm_model_id") or ""),
        "configured_endpoint": str(services.get("chat_llm_remote_url") or ""),
        "configured_max_tokens": chat_llm.max_tokens(services),
        "api_key_set": bool(key),
        "api_key_masked": ("*" * 12 + key[-4:]) if len(key) > 4 else ("*" * len(key)),
    }


@router.get("/api/v1/assistant/models")
def assistant_models(url: str = "", provider: str = ""):
    """The models the assistant's endpoint serves -- or those of a candidate the form is holding.

    `/api/v1/llm/models` answers for the *configured* provider, which is the pipeline's, so that
    list is the wrong one for an assistant pointed at a llama.cpp on the LAN. Worse for this use:
    its remote branch returns the built-in catalogue prepended to whatever the server said, so an
    address that never answered still comes back with a populated list. This asks the address
    itself and says which of the two the answer came from, which is the whole question the field
    it fills is asking.

    `url` and `provider` are the values *typed* in the form, so a list can be offered the moment
    an address is entered -- before anything is saved. Without them the saved block answers.
    """

    from services import llm_service

    services = _services()
    candidate = str(url or "").strip()
    named = str(provider or "").strip().casefold()
    if not candidate and not named:
        endpoint = chat_llm.chat_endpoint(services)
        named = str(endpoint.get("provider") or chat_llm.chat_provider(services) or "local")
        candidate = str(endpoint.get("base_url") or services.get("chat_llm_remote_url") or "")

    if named not in chat_llm.HOSTED_PROVIDERS:
        # `local`, and an unset provider that follows the pipeline, are both answered by
        # `llm_service`, which owns llama-server -- so its catalogue is the list that can be picked.
        return {
            "models": llm_service.get_available_models(provider="local"),
            "provider": "local",
            "endpoint": "",
            "source": "catalogue",
            "reachable": None,
            "suggested_model": "",
            "detail": "",
        }

    trial = dict(services)
    trial["chat_llm_provider"] = named
    if candidate:
        trial["chat_llm_remote_url"] = candidate
    endpoint = chat_llm.chat_endpoint(trial)
    base = str(endpoint.get("base_url") or "")
    models, detail = chat_llm.list_models(base, api_key=str(endpoint.get("api_key") or ""))
    return {
        "models": models,
        "provider": named,
        "endpoint": base,
        "source": "endpoint" if models else "none",
        "reachable": bool(models),
        "suggested_model": chat_llm.suggested_model(models),
        "detail": detail,
    }


@router.post("/api/v1/assistant/settings")
async def assistant_save_settings(request: Request):
    """Save the assistant's own block.

    It persists into the same `services` dict every other setting lives in -- so the assistant's
    choice survives a restart and travels with a project -- but the write happens here because
    the panel that renders these fields is ours. That keeps the shared store's contract, and its
    file upstream owns, untouched.
    """

    import wgp

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    services = dict(wgp.server_config.get("services", {}) or {})
    # Every field of this block is saved on its own -- picking a provider, leaving the address,
    # leaving the model -- so a field the body does not mention keeps what it had. Writing the
    # provider unconditionally from an absent key is what made the select snap back to "Same as
    # the LLM above" the moment the address was saved, and with it the address and model fields
    # disappeared, which read as "it did not save".
    provider = str(services.get("chat_llm_provider") or "").strip().casefold()
    if "chat_llm_provider" in body:
        provider = str(body.get("chat_llm_provider") or "").strip().casefold()
        if provider and provider not in {"local", "remote", "openai"}:
            raise HTTPException(
                status_code=400,
                detail=f"'{provider}' is not a provider the assistant can use directly "
                "(choose remote/openai, or leave it empty to follow the pipeline)",
            )
        services["chat_llm_provider"] = provider
    for key in ("chat_llm_model_id", "chat_llm_remote_url"):
        if key in body:
            services[key] = str(body.get(key) or "").strip()
    if "chat_llm_remote_url" in body and provider in chat_llm.HOSTED_PROVIDERS:
        typed = str(services.get("chat_llm_remote_url") or "").strip()
        if typed:
            from services.llm_endpoint import remote_base

            base = remote_base(typed)
            if not base.lower().startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400,
                    detail="The server URL has to start with http:// or https:// -- for "
                    "llama.cpp on this machine, the address it was started with, such as "
                    "http://127.0.0.1:8080.",
                )
            # Stored the way the chat calls it. A pasted `/v1` is what the OpenAPI examples show,
            # and keeping the typed copy would make the field disagree with the address in use
            # by a suffix this code adds back -- which is the shape of "it did not save".
            services["chat_llm_remote_url"] = base
    if "chat_llm_max_tokens" in body:
        try:
            tokens = int(body.get("chat_llm_max_tokens") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="max_tokens must be a whole number") from None
        if not 256 <= tokens <= 16384:
            raise HTTPException(status_code=400, detail="max_tokens must be between 256 and 16384")
        services["chat_llm_max_tokens"] = tokens
    # An empty string clears the stored key; omitting the field keeps it, so a form that never
    # shows the value cannot erase it by saving.
    if "chat_llm_api_key" in body:
        services["chat_llm_api_key"] = str(body.get("chat_llm_api_key") or "").strip()
    wgp.server_config["services"] = services
    _persist_services()
    return assistant_settings()


def _persist_services() -> None:
    """Write the server config the way the Services panel's own save does.

    The same atomic replace, for the same reason: a preferences file half-written is how a
    setting is lost. It is spelled here rather than imported from `launch.py`, because that
    module is the one importing this one -- and importing it back at request time would run the
    whole server module a second time when it is the process's `__main__`.
    """

    import json
    import os

    import wgp

    config_path = os.path.abspath(wgp.server_config_filename)
    temp_path = f"{config_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with _SETTINGS_WRITE_LOCK:
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(wgp.server_config, handle, indent=4)
            os.replace(temp_path, config_path)
        finally:
            if os.path.isfile(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


@router.post("/api/v1/assistant/message")
async def assistant_message(request: Request):
    """One turn of the conversation. Reads, proposes, never writes."""

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    history = body.get("messages")
    if not isinstance(history, list) or not history:
        raise HTTPException(status_code=400, detail="messages must be a non-empty list")
    conversation: list[dict[str, Any]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        conversation.append({"role": role, "content": str(message.get("content") or "")})
    if not conversation:
        raise HTTPException(status_code=400, detail="messages must contain a user turn")
    context = _context_line(body)
    if context:
        conversation = [*conversation, {"role": "user", "content": context}]
    try:
        prose, reads, proposals = _run_loop(_services(), conversation, body)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    return {"reply": prose, "reads": reads, "proposals": proposals}


@router.post("/api/v1/assistant/apply")
async def assistant_apply(request: Request):
    """Apply one accepted proposal, through the same writer the clip cards use.

    The payload reaches `update_clip_prompt` exactly as the Director's own PUT route sends it,
    so an edit made here and an edit made on a clip card are the same edit, refused for the same
    reasons and against the same lock.
    """

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    pid = str(body.get("pipeline_id") or "").strip()
    kind = str(body.get("kind") or "")
    try:
        shot = int(body.get("shot"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="shot must be a whole number") from None
    if not pid:
        raise HTTPException(status_code=400, detail="pipeline_id is required")

    import wgp

    from services.director_pipeline import (
        PipelineBusyError,
        update_clip_prompt,
        update_clip_tag,
    )

    base = wgp.server_config.get("save_path", "outputs")
    try:
        if kind == "tag":
            tag = str(body.get("tag") or "").strip()
            if not tag:
                raise HTTPException(status_code=400, detail="tag is required")
            ok = update_clip_tag(base, pid, shot - 1, tag)
        elif kind == "prompt":
            prompt = str(body.get("prompt") or "")
            if not prompt.strip():
                raise HTTPException(status_code=400, detail="prompt is required")
            ok = update_clip_prompt(base, pid, shot - 1, {"video_prompt": prompt})
        else:
            raise HTTPException(
                status_code=400, detail=f"'{kind}' is not a proposal this can apply",
            )
    except PipelineBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not ok:
        raise HTTPException(status_code=404, detail="The saved plan could not take that edit")
    return {"applied": True, "kind": kind, "shot": shot}
