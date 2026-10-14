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
  and Strata accepts the first, which is already an OpenAI field. But ``none`` is only the level the
  model was trained to obey, not a limit: measured on a planning call, the model thought for
  **24,605 tokens in 700 seconds** and answered with 2,911 characters of a plan that was then
  rejected -- 11.7 minutes for one call, paid again by the repair pass. Strata's own log says it
  seventeen times: "the reply reached max tokens while still thinking, so it has no answer", and
  names the field that fixes it. A positive ``reasoning_budget_tokens`` is a **hard** cap (the
  server closes the thinking and answers from what it holds, without re-reading), so a caller that
  refused thinking now sends one. Zero is not the value: for this endpoint ``0`` means *no cap*;
- a requested schema sent in llama.cpp's form is misread (the schema came back as the answer), while
  the **OpenAI standard** form -- ``{"type": "json_schema", "json_schema": {"name", "strict",
  "schema"}}`` -- returned JSON that conformed to the schema exactly;
- that form obeys OpenAI's rule about the schema **root**: it "must accept only JSON objects at its
  root (type object, or anyOf/oneOf of object schemas)". llama.cpp compiles an array root happily, so
  a shot list or a table read -- which constrain an array -- has always crossed the local path and
  never crossed this one: the endpoint answers an array root with an immediate 400, before a token is
  generated, and the call is lost.

Wrapping the array under an object root was measured too, and is worse than not sending it. The
endpoint's own directive says "Return exactly one JSON object matching the JSON Schema below", so a
request for 16-22 shots came back as **one shot** (2533 characters, then 3919 on the repair pass,
`finish=stop`) and the plan failed with "returned 1 shots; expected 16-30". The text of a schema is
the only lever this endpoint has on the shape of an answer, and that lever pulls every answer toward
a single object. An array schema therefore keeps the unconstrained request it has always had, which
is also what every hosted API gets.

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

# A caller that refused thinking still gets a short allowance: the endpoint's own `none` level is an
# instruction the model may ignore, and when it does, it thinks until the budget is gone (measured:
# 24,605 tokens, 11.7 minutes, a rejected plan). This is a hard cap -- the server closes the thinking
# there and answers from what it holds. Zero must not be used here: for this endpoint 0 means no cap.
_THINKING_CAP_TOKENS = 512

# The one field this module adds that is not in OpenAI's chat schema. It is documented by the
# endpoint (`reasoning_budget_tokens`, DETAILS.md "A hard thinking budget"), it is what that
# endpoint's own error message asks for, and it is never sent to an endpoint that did not name
# itself as this dialect.
_DIALECT_ONLY_FIELDS = frozenset({"reasoning_budget_tokens"})

# url -> (monotonic reading, answer)
_probe_cache: dict[str, tuple[float, bool]] = {}

# url -> (monotonic reading, answer); a separate cache because the two probes read
# different fields and a server may answer one and not the other.
_images_cache: dict[str, tuple[float, bool]] = {}

# endpoints already told that their schema rule and a batch of shots disagree, so the
# notice names the cause once instead of once per call.
_root_notice: set[str] = set()


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
        _root_notice.discard(str(url).strip())
    else:
        _probe_cache.clear()
        _images_cache.clear()
        _root_notice.clear()


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


def _object_rooted(schema: Mapping[str, Any]) -> bool:
    """Whether an endpoint that follows OpenAI's rule would accept this schema.

    The rule is the root's: "type object, or anyOf/oneOf of object schemas". A union is accepted only
    when every branch is an object, because one branch the server cannot hold a root for is a 400.
    """

    if schema.get("type") == "object":
        return True
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list) and branches:
            if all(isinstance(branch, Mapping) and branch.get("type") == "object" for branch in branches):
                return True
    return False


# The envelope the planner's own parser already unwraps: every branch of ``_parse_json_response``
# reads the list under "shots". Kept named here because the wrapper was measured and rejected, and
# the reason belongs next to the key it turned on.
ENVELOPE_KEY = "shots"


def _object_root(schema: Mapping[str, Any]) -> dict:
    """An array schema moved under the object root this endpoint insists on.

    Measured and unused: the endpoint then asks for "exactly one JSON object", and the model answers
    with one shot. Undo the wrapping in ``finalize`` only with a new measurement in hand.
    """

    return {
        "type": "object",
        "properties": {ENVELOPE_KEY: dict(schema)},
        "required": [ENVELOPE_KEY],
        "additionalProperties": False,
    }


def _say_root_once(url: str, wrapped: bool) -> None:
    key = str(url or "").strip()
    if key in _root_notice:
        return
    _root_notice.add(key)
    if wrapped:
        print(
            f"[LLM] {key} requires an object at the root of a JSON schema and this request constrains "
            f'an array, so it is sent as an object with one "{ENVELOPE_KEY}" field.'
        )
        return
    print(
        f"[LLM] {key} requires an object at the root of a JSON schema and this request constrains an "
        "array (a shot list or a table read). Sending it unconstrained, as it has always been here."
    )


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
        schema = grammar["schema"]
        if _object_rooted(schema):
            out["response_format"] = _openai_schema(schema)
        else:
            # Measured, twice. Sending it as an array root is an immediate 400 ("must accept only
            # JSON objects at its root") and the call is lost. Wrapping the array under an object
            # root is accepted, but the endpoint then states "return exactly one JSON object" to the
            # model, and a 16-22 shot request came back as ONE shot (2533 characters). Neither is
            # usable, so an array schema keeps the unconstrained request it has always had here.
            out.pop("response_format", None)
            _say_root_once(url, wrapped=False)
    if _thinking_was_refused(raw):
        # The reasoning is billed against the same allowance as the answer, and a
        # structured caller only asked for the JSON. The level alone is not enough,
        # so it is capped: see _THINKING_CAP_TOKENS.
        out["reasoning_effort"] = "none"
        out["reasoning_budget_tokens"] = _THINKING_CAP_TOKENS
    return out
