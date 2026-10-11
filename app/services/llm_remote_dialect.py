"""What a local OpenAI-compatible server needs to hear in Maestro's dialect.

Maestro's remote path deliberately sends the OpenAI common denominator: it drops the llama.cpp
extensions (``enable_thinking``, ``chat_template_kwargs``) and, because only the bundled
llama-server can compile a grammar, it drops a requested JSON schema too and calls out
unconstrained. That is the right default for a hosted API.

It is the wrong default for a local server that speaks more than the common denominator. Measured
against Strata (``qwen3.8-flash-next-iq2_xs`` on ``127.0.0.1:8081``):

- the model reasons before it answers, so on a tight allowance the whole budget went to
  ``reasoning_content`` and ``content`` came back empty -- 80 tokens generated, 337 characters of
  reasoning, nothing to show;
- ``reasoning_effort: "none"`` and ``chat_template_kwargs.enable_thinking=false`` both silence that,
  and Strata accepts the first, which is already an OpenAI field;
- a requested schema sent in llama.cpp's form is misread (the schema came back as the answer), while
  the **OpenAI standard** form -- ``{"type": "json_schema", "json_schema": {"name", "strict",
  "schema"}}`` -- returned JSON that conformed to the schema exactly.

So the two fields Maestro needs are the ones it already knows how to send; it just had no reason to
believe the endpoint could use them. This module supplies that reason: a server says what it is on
its own ``/health``, and only a server on this machine or the LAN is asked at all, so a hosted API
never sees an extra request. One unanswered probe means the common denominator -- the behaviour
every other endpoint already gets.

The same probe answers a second question the remote path used to guess: whether images can be sent
at all. ``load_model`` set ``_vision_available = True`` for every remote writer and left it to the
provider to complain. Measured against Strata: an image block comes back **400 Bad Request**, in the
middle of a plan, on a character reference the writer was supposed to read. An endpoint that says
what it accepts is believed instead; one that says nothing keeps the optimistic behaviour, because a
hosted API cannot be probed and dropping its images would break what works today.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from services.llm_writer_health import is_local_endpoint

__all__ = [
    "DEFAULT_URL",
    "SERVICE_NAME",
    "applies",
    "finalize",
    "forget",
    "speaks_dialect",
    "takes_images",
]

SERVICE_NAME = "strata"
DEFAULT_URL = "http://127.0.0.1:8081"

_HEALTH_PATH = "/health"
_MODELS_PATH = "/v1/models"
_PROBE_TIMEOUT = 2.0
_PROBE_TTL = 60.0

# url -> (monotonic reading, answer)
_probe_cache: dict[str, tuple[float, bool]] = {}

# url -> (monotonic reading, answer); a separate cache because the two probes read
# different fields and a server may answer one and not the other.
_images_cache: dict[str, tuple[float, bool]] = {}


def _probe(url: str) -> bool:
    import requests

    from services.llm_endpoint import remote_base

    response = requests.get(f"{remote_base(url)}{_HEALTH_PATH}", timeout=_PROBE_TIMEOUT)
    if not response.ok:
        return False
    body = response.json() or {}
    return str(body.get("service") or "").strip().lower() == SERVICE_NAME


def speaks_dialect(url: str, *, now: float | None = None) -> bool:
    """Whether the server at this URL introduces itself as one that speaks it."""

    key = str(url or "").strip()
    if not key or not is_local_endpoint(key):
        return False
    moment = time.monotonic() if now is None else now
    cached = _probe_cache.get(key)
    if cached is not None and moment - cached[0] < _PROBE_TTL:
        return cached[1]
    try:
        detected = _probe(key)
    except Exception:
        detected = False  # A server that does not answer is not one of ours.
    _probe_cache[key] = (moment, detected)
    return detected


def forget(url: str = "") -> None:
    """Drop cached detection: for a test, or after the server was restarted."""

    if url:
        _probe_cache.pop(str(url).strip(), None)
        _images_cache.pop(str(url).strip(), None)
    else:
        _probe_cache.clear()
        _images_cache.clear()


def _probe_images(url: str) -> Optional[bool]:
    """What the server says about image input: True, False, or None when it does not say."""

    import requests

    from services.llm_endpoint import remote_base

    base = remote_base(url)
    try:
        health = requests.get(f"{base}{_HEALTH_PATH}", timeout=_PROBE_TIMEOUT)
        if health.ok:
            body = health.json() or {}
            if "images" in body:
                return bool(body.get("images"))
        listed = requests.get(f"{base}{_MODELS_PATH}", timeout=_PROBE_TIMEOUT)
        if listed.ok:
            for entry in (listed.json() or {}).get("data") or []:
                modalities = ((entry or {}).get("architecture") or {}).get("input_modalities")
                if isinstance(modalities, list):
                    return any(str(mode).strip().lower() == "image" for mode in modalities)
    except Exception:
        return None
    return None


def takes_images(url: str, *, now: float | None = None) -> bool:
    """Whether images can be sent here. Unknown means yes, which is what it always was.

    Two fields are read, in the order a server is likely to answer: ``images`` on ``/health``
    (Strata reports ``false``), then ``input_modalities`` of the first model on ``/v1/models`` (a
    llama.cpp or LM Studio style listing). Nothing else is inferred: a server that says nothing is
    treated as capable, so a hosted API -- which cannot be asked -- keeps sending the images it
    accepts today.
    """

    key = str(url or "").strip()
    if not key or not is_local_endpoint(key):
        return True
    moment = time.monotonic() if now is None else now
    cached = _images_cache.get(key)
    if cached is not None and moment - cached[0] < _PROBE_TTL:
        return cached[1]
    answer = _probe_images(key)
    capable = True if answer is None else answer
    _images_cache[key] = (moment, capable)
    if not capable:
        # Said once, where the writer is connected, because a silent drop is how a reference image
        # goes missing without anyone noticing: the plan runs, the prompts describe a picture the
        # writer never saw, and nothing in the log mentions it.
        print(f"[LLM] {key} takes text only: reference images will not reach it.")
    return capable


def applies(provider: str, url: str) -> bool:
    """Whether this request is going to an endpoint that can use the dialect."""

    return str(provider or "") in {"remote", "openai"} and speaks_dialect(url)


def _thinking_was_refused(raw: Mapping[str, Any]) -> bool:
    """Whether the caller asked for an answer with no reasoning in front of it."""

    if raw.get("enable_thinking") is False:
        return True
    kwargs = raw.get("chat_template_kwargs")
    return isinstance(kwargs, Mapping) and kwargs.get("enable_thinking") is False


def _openai_schema(json_schema: Mapping[str, Any]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": "maestro_response", "strict": True, "schema": dict(json_schema)},
    }


def finalize(
    provider: str,
    url: str,
    raw: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> dict:
    """Turn the translated payload into the one this endpoint should receive.

    Without the dialect, nothing changes: the llama.cpp grammar request is dropped and the call goes
    out unconstrained, exactly as it did before, only now the reason is said once here instead of at
    each site that builds a payload.
    """

    grammar = raw.get("response_format")
    has_grammar = isinstance(grammar, Mapping) and isinstance(grammar.get("schema"), Mapping)

    if not applies(provider, url):
        out = dict(prepared)
        if has_grammar:
            out.pop("response_format", None)
            print(
                f"[LLM] json_schema requested but provider={provider} is not a known "
                "strict-JSON server — sending unconstrained"
            )
        return out

    out = dict(prepared)
    if has_grammar:
        out["response_format"] = _openai_schema(grammar["schema"])
    if _thinking_was_refused(raw):
        # The reasoning is billed against the same allowance as the answer, and a
        # structured caller only asked for the JSON.
        out["reasoning_effort"] = "none"
    return out
