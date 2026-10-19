"""The assistant's own endpoint, so chatting cannot disturb the pipeline.

`llm_service` holds one model for the whole app and every Director pass loads through it. That
is right for planning -- one resident model, reused -- and wrong for a chat, which is
interactive and may be used while clips are rendering:

  * on the shared local path, a chat turn waits for the generation slot and then competes for
    the same GPU the video model is holding, which is the contention `llm_writer_supervision`
    exists to prevent;
  * choosing a hosted provider through `llm_service` unloads the pipeline's local model, so the
    next planning pass pays the reload.

So the assistant can be given its own provider, model and endpoint. A llama.cpp on this machine
or on the LAN is reached the way every other OpenAI-compatible server is -- provider ``remote``
with its own URL -- and the pipeline's writer is never loaded, unloaded or asked for anything.

Two answers are deliberately deferred to `llm_service` instead: an empty provider ("same as the
pipeline") and ``local``. A local model has to go through that service, because it is what owns
llama-server, and pretending otherwise would start a second server on the same GPU.

Anthropic is deliberately **not** offered here. Its API wants its own headers and its own body
shape, which is a second dialect this path does not implement; offering it would be a control that
fails on first use. It is reachable the other way -- set the pipeline's provider to Anthropic and
leave the assistant unset, so the assistant follows the pipeline through `llm_service`, which does
speak it.

The reply carries prose plus an optional block of actions. That is a contract rather than
native tool-calling on purpose: nothing in this repository speaks ``tool_calls``, and a
quantized local model asked for the native form tends to invent it. Every endpoint that can
hold a JSON block can hold this one.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ACTIONS_HINT",
    "DEFAULT_MAX_TOKENS",
    "HOSTED_PROVIDERS",
    "MODEL_PROBE_TIMEOUT",
    "SETTING_KEYS",
    "ask",
    "chat_endpoint",
    "chat_provider",
    "describe",
    "list_models",
    "parse_actions",
    "suggested_model",
    "vision_capable",
]

# The five keys the Settings panel writes. The URL and the key are the assistant's own, so it can
# point at a llama.cpp on the LAN while the pipeline talks to something else; leaving them empty
# falls back to the shared remote endpoint, which is the common case of one endpoint for both.
SETTING_KEYS = (
    "chat_llm_provider",
    "chat_llm_model_id",
    "chat_llm_remote_url",
    "chat_llm_api_key",
    "chat_llm_max_tokens",
)

_HOSTED = HOSTED_PROVIDERS = frozenset({"remote", "openai"})

# The one field this module sends that a strict OpenAI endpoint would not know: the dialect reads
# it and drops it, and everything else in the request is the standard shape.
_DIALECT_ONLY_INTENT = frozenset({"enable_thinking"})

DEFAULT_TIMEOUT = 300.0
# A model list is asked for while somebody is looking at the field, so it is bounded by patience
# rather than by the endpoint: an address that is not answering must not hold the panel open for
# the ten seconds a render's own call budget would allow.
MODEL_PROBE_TIMEOUT = 6.0
DEFAULT_MAX_TOKENS = 2048
MAX_IMAGES = 4
# A conversation that grows without bound stops fitting a light model's window, and the oldest
# turns are the ones it needs least. Twelve turns is a working session rather than an archive.
MAX_TURNS = 12

DATA_URL_PREFIX = "data:"
ACTIONS_HINT = (
    "To read something you do not have, or to propose an edit, end your answer with one fenced "
    "```json block holding {\"actions\": [...]} and nothing else in the block. Available tools:\n"
    "- {\"tool\": \"get_pipeline_state\"} -- the current plan: shots, strategies, durations, what "
    "is rendered.\n"
    "- {\"tool\": \"get_clip_prompt\", \"shot\": N} -- the compiled prompt of one shot, plus the "
    "draft it was polished from.\n"
    "- {\"tool\": \"get_prompt_audit\"} -- the contradictions the prompt audit found.\n"
    "- {\"tool\": \"get_recent_logs\", \"lines\": N} -- the tail of the render log.\n"
    "- {\"tool\": \"get_project_text\"} -- the project's shared brief.\n"
    "- {\"tool\": \"propose_clip_prompt\", \"shot\": N, \"prompt\": \"...\"} -- the whole new "
    "prompt for one shot. It is checked against the prompt contract and then shown to the user, "
    "who decides whether to apply it. Never claim it is applied.\n"
    "- {\"tool\": \"propose_clip_tag\", \"shot\": N, \"tag\": \"...\"} -- a short label for one shot.\n"
    "One block per answer, at most three actions, and no action when prose is enough."
)

_ACTION_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_ACTION_BLOCK = re.compile(r"\{\s*\"actions\"\s*:\s*\[.*?\]\s*\}", re.DOTALL)


def chat_provider(services: Any) -> str:
    """The provider the assistant runs on, or "" to follow the pipeline's."""

    if not isinstance(services, Mapping):
        return ""
    return str(services.get("chat_llm_provider") or "").strip().casefold()


def chat_endpoint(services: Any) -> dict:
    """Where a chat turn is sent, or {} when `llm_service` should answer it.

    The endpoint reuses the shared remote URL and the provider's credential when the assistant
    has none of its own, so the common case -- one endpoint for the whole app -- needs one
    choice rather than a second copy of the same two fields.

    The model follows the same rule, and only that far: it is borrowed from the pipeline when the
    assistant has none *and* is pointed at the pipeline's own address. A different address means a
    different server, whose models are not the pipeline's, so the endpoint comes back with an
    empty model -- which `describe` and the route report, rather than a turn that fails later.
    """

    provider = chat_provider(services)
    if provider not in _HOSTED:
        return {}
    from services import llm_service
    from services.llm_endpoint import remote_base

    own_url = str((services or {}).get("chat_llm_remote_url") or "").strip()
    shared_url = str((services or {}).get("llm_remote_url") or "").strip()
    url = own_url or shared_url
    model = str((services or {}).get("chat_llm_model_id") or "").strip()
    if not model and (
        not own_url or _same_address(remote_base(own_url), remote_base(shared_url))
    ):
        # The pipeline's model answers for the pipeline's own address. Borrowing it for a
        # different one -- another machine on the LAN -- asks that server for a model it does not
        # serve, so the turn fails with a 404 the reader blames on the setting they just made.
        model = str((services or {}).get("llm_model_id") or "").strip()
    if not remote_base(url):
        return {}
    return {
        "provider": provider,
        "model": model,
        "base_url": remote_base(url) if provider in _HOSTED else "",
        "api_key": str((services or {}).get("chat_llm_api_key") or "").strip()
        or llm_service.provider_api_key(provider, services),
    }


def shares_the_pipeline_writer(services: Any, endpoint: Mapping[str, Any]) -> bool:
    """Whether a chat turn is answered by the same engine the rest of the app writes with.

    Two ways that happens, and both are contended: the assistant has no endpoint of its own, so
    `llm_service` answers from the pipeline's model; or it is pointed at the very same address as
    the pipeline's writer. The second is the one a person would not expect -- an assistant given
    the writer's own URL looks separate while every chat turn queues behind whatever is rendering.
    """

    if not endpoint:
        return True
    from services.llm_endpoint import remote_base

    return _same_address(
        endpoint.get("base_url"),
        remote_base((services or {}).get("llm_remote_url") or ""),
    )


def _same_address(one: Any, other: Any) -> bool:
    """Whether two endpoints are the same address, however each of them spells it."""

    def normalise(value: Any) -> str:
        text = str(value or "").strip().rstrip("/").casefold()
        for prefix in ("http://", "https://"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text.rstrip("/")

    first, second = normalise(one), normalise(other)
    return bool(first) and first == second


def describe(endpoint: Mapping[str, Any]) -> str:
    """One line naming what a chat turn will really use."""

    if not endpoint:
        return "the pipeline's LLM"
    where = endpoint.get("base_url") or endpoint.get("provider")
    return f"{endpoint.get('model')} at {where}"


def max_tokens(services: Any) -> int:
    """The answer's budget. A chat reply is long-form; a summary is not."""

    try:
        value = int((services or {}).get("chat_llm_max_tokens") or DEFAULT_MAX_TOKENS)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return max(256, min(16384, value))


def vision_capable(endpoint: Mapping[str, Any], services: Any = None) -> bool:
    """Whether an image may be sent here.

    A local endpoint is asked and believed (`llm_remote_dialect.takes_images`); a hosted API
    cannot be asked and keeps the optimistic answer, because dropping the image would break the
    case that works today. A llama.cpp publishes neither field the probe reads, so it counts as
    unknown -- which is correct here, and why a rejected image is retried without it rather than
    failing the turn.
    """

    from services.llm_remote_dialect import takes_images

    if endpoint:
        url = str(endpoint.get("base_url") or "")
        if not url:
            return True
        return takes_images(url)
    # The pipeline's own path takes images through llm_service, which sends them to whatever
    # provider it is configured with.
    return True


def list_models(
    url: str, *, api_key: str = "", timeout: float = MODEL_PROBE_TIMEOUT,
) -> tuple[list[dict[str, Any]], str]:
    """The models a candidate endpoint reports, and why it reported none.

    Returns ``(models, detail)``, where ``detail`` is ``""`` when the endpoint answered. The
    caller is a field being filled in, so nothing here is fatal: an address that is wrong, down,
    or not an OpenAI-compatible server is a sentence to show next to the field rather than an
    exception that loses the rest of the panel.

    This asks the candidate URL directly rather than through `llm_service.get_available_models`,
    for two reasons that both matter here: that call returns the built-in local catalogue
    *prepended* to whatever the remote answered, so an unreachable server is indistinguishable
    from one that replied -- the list looks populated either way -- and it sends a masked
    credential, so a server that requires a key answers 401.
    """

    import requests

    from services.llm_endpoint import models_url

    target = models_url(url)
    if not str(url or "").strip():
        return [], "No server URL yet."
    headers = {"Authorization": f"Bearer {api_key}"} if str(api_key or "").strip() else {}
    try:
        response = requests.get(target, headers=headers, timeout=float(timeout))
    except Exception as error:  # noqa: BLE001 -- any transport failure is the same sentence
        return [], f"{target} did not answer ({error})."
    if not response.ok:
        return [], f"{target} answered {response.status_code}: {str(response.text)[:200]}"
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 -- a page that is not JSON is not a model list
        return [], f"{target} did not answer with a model list."
    models: list[dict[str, Any]] = []
    for entry in data.get("data") or []:
        model_id = str((entry or {}).get("id") or "").strip()
        if model_id:
            models.append({"id": model_id, "label": model_id, "size_hint": "", "provider": "remote"})
    if not models:
        return [], f"{target} listed no models."
    return models, ""


def suggested_model(models: Sequence[Mapping[str, Any]]) -> str:
    """The one model to choose without asking, or "" when the choice is the user's.

    A server that publishes a single model has already answered the question the field asks; two
    or more is a decision, and picking the first of them would be a guess written into a setting.
    """

    if len(models) == 1:
        return str(models[0].get("id") or "")
    return ""


def _data_url(path: str) -> str:
    """One image as a data URL, the shape every OpenAI-compatible endpoint accepts."""

    resolved = os.path.realpath(str(path or ""))
    if not os.path.isfile(resolved):
        raise ValueError(f"The image does not exist: {path}")
    mime = mimetypes.guess_type(resolved)[0] or "image/png"
    with open(resolved, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"{DATA_URL_PREFIX}{mime};base64,{encoded}"


def _content_with_images(text: str, images: Sequence[str]) -> Any:
    """A user turn as text, or as text plus image blocks when there are images.

    With no image the content stays a plain string: the block form is valid, but a couple of
    OpenAI-compatible servers handle it less well, and there is no reason to ask them to.
    """

    if not images:
        return str(text or "")
    blocks: list[dict[str, Any]] = []
    if str(text or "").strip():
        blocks.append({"type": "text", "text": str(text)})
    for path in images:
        blocks.append({"type": "image_url", "image_url": {"url": _data_url(path)}})
    return blocks


def trim_turns(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The conversation, with the system prompt kept and the oldest turns dropped."""

    kept = [dict(message) for message in messages if isinstance(message, Mapping)]
    system = [message for message in kept if message.get("role") == "system"]
    rest = [message for message in kept if message.get("role") != "system"]
    return [*system, *rest[-MAX_TURNS:]]


def parse_actions(reply: str) -> tuple[str, list[dict[str, Any]]]:
    """The prose and the actions in one answer.

    Prose is what is left once the block is removed, so a model that answers in one paragraph
    and then asks for a tool reads normally. An answer with no block, or with a block that is
    not this shape, keeps all of its text and asks for nothing: a stray brace in an explanation
    is not a reason to lose the explanation.
    """

    text = str(reply or "")
    # The fenced form is what the hint asks for; the bare object is what a model that forgot the
    # fence writes. Both are read, and whichever matched is what gets removed from the prose.
    match = _ACTION_BLOCK.search(text)
    block = match.group(1) if match else None
    if match is None:
        match = _BARE_ACTION_BLOCK.search(text)
        block = match.group(0) if match else None
    if match is None or block is None:
        return text.strip(), []
    try:
        payload = json.loads(block)
    except Exception:
        return text.strip(), []
    raw = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return text.strip(), []
    actions = [action for action in raw if isinstance(action, dict) and action.get("tool")]
    prose = (text[: match.start()] + text[match.end():]).strip()
    return prose, actions[:3]


def _post_chat(endpoint: Mapping[str, Any], messages: list[dict[str, Any]], *, extra: dict) -> str:
    """One call, sent the way every other Maestro writer call is sent.

    This goes through `llm_remote_dialect`, and that is not decoration. For a Strata endpoint the
    dialect is what turns "I want an answer, not a deliberation" into `reasoning_effort: none`
    plus a hard `reasoning_budget_tokens` cap. Posting straight to `/v1/chat/completions` -- which
    is what this did first -- left the reasoning uncapped, and measured on that server an
    uncapped call ran to **24,605 tokens in 700 seconds** with nothing to show. One such call
    occupies the engine, so the pipeline's own planning measures slower while it waits, which is
    the reported "the assistant loads and pulls resources".
    """

    import requests

    from services import llm_remote_dialect

    provider = str(endpoint.get("provider") or "")
    base = str(endpoint.get("base_url") or "")
    if not base:
        raise ValueError(
            "The assistant needs an endpoint: set the Server URL for the provider chosen above it."
        )
    if not str(endpoint.get("model") or "").strip():
        # Not a silent fall back to the pipeline's model: this endpoint is a different server, so
        # the pipeline's model name means nothing to it and the 404 would read as a broken setting.
        raise ValueError(
            f"The assistant has no model for {base}. Choose one below it: the endpoint's own "
            "list is offered there, and a server that publishes one model gets it filled in."
        )
    # What the caller wants, in Maestro's own dialect. `enable_thinking: False` is the request the
    # dialect reads: it means "answer me", and a Strata endpoint caps the reasoning for it. It is
    # the only non-standard field sent here, so the common denominator is everything else.
    intent: dict[str, Any] = {
        "model": endpoint.get("model"),
        "messages": messages,
        "max_tokens": int(extra.get("max_tokens") or DEFAULT_MAX_TOKENS),
        "temperature": float(extra.get("temperature") or 0.4),
        "stream": False,
        "enable_thinking": False,
    }
    prepared = {key: value for key, value in intent.items() if key not in _DIALECT_ONLY_INTENT}
    payload = llm_remote_dialect.finalize(provider, base, intent, prepared)

    headers = {"Content-Type": "application/json"}
    if endpoint.get("api_key"):
        headers["Authorization"] = f"Bearer {endpoint['api_key']}"
    response = requests.post(
        f"{base}/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=float(extra.get("timeout") or DEFAULT_TIMEOUT),
    )
    if not response.ok:
        raise RuntimeError(
            f"The assistant's endpoint at {base} refused the request "
            f"({response.status_code}): {response.text[:400]}"
        )
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"The assistant's endpoint returned no answer: {json.dumps(data)[:300]}")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        # A reasoning model that spent its allowance thinking, and a provider that declined, both
        # read as "empty answer" from the outside and need different answers from the reader.
        reason = str(choices[0].get("finish_reason") or "")
        usage = data.get("usage") or {}
        detail = usage.get("completion_tokens_details") or {}
        facts = [f"finish_reason={reason}"] if reason else []
        if usage.get("completion_tokens") is not None:
            facts.append(f"completion_tokens={usage['completion_tokens']}")
        if detail.get("reasoning_tokens") is not None:
            facts.append(f"reasoning_tokens={detail['reasoning_tokens']}")
        raise RuntimeError(
            "The assistant's endpoint returned an empty answer "
            f"[{'; '.join(facts) or 'no finish_reason or usage reported'}]"
            + (
                "; the allowance ran out before it wrote the answer, so raise Answer length"
                if reason == "length" else ""
            )
        )
    return content


def _transcript(system: str, messages: Sequence[Mapping[str, Any]], images: Sequence[str]) -> str:
    """A conversation as one prompt, for the single-turn path through `llm_service`."""

    lines = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content)[:2000]
        lines.append(f"{'User' if role == 'user' else 'Assistant'}: {content.strip()}")
    if images:
        lines.append(
            f"[{len(images)} image(s) are attached to this turn.]"
        )
    return "\n\n".join(lines).strip()


def ask(
    services: Any,
    messages: Sequence[Mapping[str, Any]],
    *,
    images: Sequence[str] = (),
    system: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """One answer from the assistant's endpoint, or from the pipeline's LLM.

    The image is dropped and the turn retried when the endpoint rejects it: a llama.cpp without
    its projector, or a provider that does not take pictures, should cost the picture rather
    than the answer. The retry is reported in the raised message only if it also fails.
    """

    conversation = trim_turns(messages)
    endpoint = chat_endpoint(services)
    wanted = list(images)[:MAX_IMAGES]
    if wanted and not vision_capable(endpoint, services):
        wanted = []
    if endpoint:
        if wanted:
            for index, message in enumerate(conversation):
                if message.get("role") == "user":
                    conversation[index] = {
                        **message,
                        "content": _content_with_images(str(message.get("content") or ""), wanted),
                    }
                    break
        extra = {"max_tokens": max_tokens(services), "timeout": timeout}
        try:
            return _post_chat(endpoint, conversation, extra=extra)
        except RuntimeError:
            if not wanted:
                raise
            # One retry without the pictures: a llama.cpp without its projector, or a provider
            # that takes text only, should cost the picture rather than the answer.
            print("[Assistant] The endpoint refused the image; retrying the turn as text only.")
            return _post_chat(endpoint, trim_turns(messages), extra=extra)

    from services import llm_service

    if not llm_service.is_loaded():
        raise RuntimeError(
            "The assistant has no endpoint of its own and the pipeline's LLM is not loaded: "
            "choose a provider for the assistant in Settings, or load the pipeline's model."
        )
    return llm_service.generate(
        _transcript(system, conversation, wanted),
        system_prompt=system,
        max_new_tokens=max_tokens(services),
        temperature=0.4,
        image_paths=list(wanted) or None,
    )
