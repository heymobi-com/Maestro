"""The shot-correction stage's own endpoint, independent of the pipeline's LLM.

`llm_service` holds one model for the whole app, and every Director pass loads
through it. That is right for planning -- one resident model, reused -- and wrong
for correction, which is interactive and happens while clips are rendering:

  * a correction on a hosted endpoint calls `load_model(provider="remote")`, which
    unloads the pipeline's local model (`if _process is not None: _unload_inner()`),
    so the next planning pass pays the reload;
  * a correction on a local model cannot even start while the video model holds the
    GPU: it waits for the generation slot and then often cannot come up.

So when the editing stage is given its own provider, it talks to that endpoint
directly and leaves `llm_service` completely alone: no load, no unload, no idle
timer, no GPU. The rest of the pipeline keeps using `llm_service` exactly as before.

The endpoint reuses the configured remote URL and its key, so one field is enough to
separate the editing LLM from the pipeline's.
"""

import json
from collections.abc import Mapping

# Providers whose endpoint answers over HTTP. `local` is deliberately absent: a
# local editing model has to go through llm_service, because that is what owns
# llama-server.
_HOSTED = frozenset({"remote", "openai", "anthropic"})

DEFAULT_TIMEOUT = 180.0


def editing_provider(services) -> str:
    """The provider the editing stage runs on, or "" to follow the pipeline's."""

    if not isinstance(services, Mapping):
        return ""
    return str(services.get("revision_llm_provider") or "").strip().casefold()


def editing_endpoint(services) -> dict:
    """Where a correction is sent, or {} when the pipeline's LLM should handle it.

    Reusing `llm_remote_url` and its credential is deliberate: the common case is a
    local pipeline with the editing stage on a hosted API, and that needs one
    choice, not a second copy of the endpoint settings.
    """

    provider = editing_provider(services)
    if provider not in _HOSTED:
        return {}
    from services import llm_service
    from services.llm_endpoint import remote_base

    model = str(
        (services or {}).get("revision_llm_model_id")
        or (services or {}).get("llm_model_id")
        or ""
    ).strip()
    if not model:
        return {}
    url = str((services or {}).get("llm_remote_url") or "").strip()
    if provider == "remote" and not remote_base(url):
        return {}
    return {
        "provider": provider,
        "model": model,
        "base_url": remote_base(url) if provider in {"remote", "openai"} else "",
        "api_key": llm_service.provider_api_key(provider, services),
    }


def describe(endpoint: Mapping) -> str:
    """One line naming what a correction will really use."""

    if not endpoint:
        return "the pipeline's LLM"
    where = endpoint.get("base_url") or endpoint.get("provider")
    return f"{endpoint.get('model')} at {where}"


def prepare(services, revision_model: str, shot_label: str, pid: str, load_pipeline_model):
    """Resolve the editing endpoint, say which one, and load only if needed.

    Returns the endpoint, or {} when the pipeline's own LLM answers. `load_pipeline_model`
    is called -- with the parameters it expects -- only on that path, which is what keeps
    a correction from touching the pipeline's resident model.
    """

    endpoint = editing_endpoint(services)
    if endpoint:
        print(f"[Pipeline {pid}] {shot_label}: correcting with {describe(endpoint)}")
        return endpoint
    if revision_model:
        print(f"[Pipeline {pid}] {shot_label}: correcting with {revision_model}")
    # Every other Director pass loads the LLM before calling it; this one called
    # enhance_prompt straight away and died with "LLM not loaded. Call load_model()
    # first." whenever nothing had been planned in that session.
    load_pipeline_model({"llm_model_id": revision_model} if revision_model else {})
    return {}


def ask(system: str, task, *, extra: str = "", endpoint, nsfw: bool, pipeline) -> str:
    """One answer: from the editing endpoint when there is one, else from the pipeline."""

    text = "\n".join([*task, *(["", extra] if extra else [])])
    if endpoint:
        return complete(system, text, endpoint=endpoint, nsfw=nsfw)
    return pipeline(text)


def complete(
    system: str,
    user: str,
    *,
    endpoint: Mapping,
    nsfw: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """One chat completion from the editing endpoint, not from llm_service.

    The system prompt gets the same content guidance `enhance_prompt` injects for
    an override caller, so mature mode behaves identically on either path. Nothing
    else of that function applies: the override is self-contained, and the thinking
    suppression it needs is a llama.cpp concern, not a hosted one.
    """

    import requests

    from services.director.nsfw_guidance import inject_content_guidance

    payload = {
        "model": endpoint.get("model"),
        "messages": [
            {"role": "system", "content": inject_content_guidance(system, nsfw, "enhance")},
            {"role": "user", "content": user},
        ],
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": False,
    }
    base = str(endpoint.get("base_url") or "")
    if not base:
        raise ValueError(
            "The editing LLM needs an endpoint: set the Server URL for the "
            "correction model's provider."
        )
    headers = {"Content-Type": "application/json"}
    if endpoint.get("api_key"):
        headers["Authorization"] = f"Bearer {endpoint['api_key']}"
    response = requests.post(
        f"{base}/v1/chat/completions", json=payload, headers=headers, timeout=timeout
    )
    if not response.ok:
        # The provider's own words: "Model Not Exist", "Authentication Fails", ...
        raise RuntimeError(
            f"The editing LLM at {base} refused the request "
            f"({response.status_code}): {response.text[:400]}"
        )
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"The editing LLM returned no answer: {json.dumps(data)[:300]}")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "")
    if not content.strip():
        raise RuntimeError("The editing LLM returned an empty answer.")
    return content
