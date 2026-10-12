"""Whether the writer is really there, and what to say when it is not.

A remote writer is never loaded by Maestro, so nothing about it can be inferred from this machine:
the only honest answer is the server's own. Before this, "loaded" meant no more than "a model id is
set", so a stopped server still showed as loaded and the failure arrived later, mid-planning, as a
connection error -- with the run half done and nothing naming the address to go and start.

Two rules keep that honest without making noise:

- only an address on this machine or the LAN is probed, because a hosted API's reachability is not
  something the operator can fix here and polling it would be both pointless and impolite;
- the answer is cached for a few seconds, so a status poll does not become a stream of requests to
  something that is already known to be up.

When the address is ours and it stops answering, the error the pipeline shows says so, and the
cached answer is dropped first, so the next status is a fresh reading rather than the stale one.
"""

from __future__ import annotations

import ipaddress
import time
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "answers",
    "forget",
    "is_connection_failure",
    "is_local_endpoint",
    "last_writer",
    "not_loaded_message",
    "unreachable_hint",
]

_REMOTE_PROVIDERS = {"remote", "openai", "anthropic"}
_TTL = 10.0
_TIMEOUT = 2.0

# How long a caller that merely asks whether the writer is there waits for a configured start. The
# pipeline retries; a status poll must not.
_ON_NEED_WAIT = 120.0

# A connection that never reached the server, by the name requests and the
# standard library give it. A status code means the server did answer.
_CONNECTION_FAILURES = {
    "ConnectionError",
    "ConnectTimeout",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "MaxRetryError",
    "NewConnectionError",
    "ReadTimeout",
    "RemoteDisconnected",
    "Timeout",
}

# address -> (monotonic reading, answer)
_cache: dict[str, tuple[float, bool]] = {}

# The writer Maestro last asked about: (provider, address). Recorded here so a module that has to act
# on the writer (releasing its memory before a render) learns the address the same way this module
# does -- from what the service told us -- instead of reaching into llm_service's own fields.
_writer: tuple[str, str] = ("", "")


def last_writer() -> tuple[str, str]:
    """The provider and address of the writer Maestro last asked about, or ("", "")."""

    return _writer


def is_local_endpoint(url: str) -> bool:
    """Whether a host is on this machine or the LAN, so asking it is cheap and safe."""

    try:
        host = urlsplit(str(url or "")).hostname or ""
    except ValueError:
        return False
    name = host.strip().lower().strip("[]")
    if name in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False  # A DNS name could be anywhere; never probe it.
    return address.is_loopback or address.is_private


def _reachable(url: str) -> tuple[bool, str]:
    """(it answers, what it says about its model). The state is "" when the server does not say."""

    import requests

    from services.llm_endpoint import models_url

    response = requests.get(models_url(url), timeout=_TIMEOUT)
    if not response.ok:
        return False, ""
    try:
        entries = (response.json() or {}).get("data") or []
    except ValueError:
        return True, ""
    if not entries:
        return True, "unloaded"  # up, and holding no model: it loads one on the next request
    status = (entries[0] or {}).get("status")
    return True, str(status.get("value") or "") if isinstance(status, dict) else "loaded"


def _note_released(address: str) -> None:
    """Say once that the next call pays for the model, instead of leaving the pause unexplained."""

    from services import llm_writer_supervision

    note = llm_writer_supervision.released_note(address)
    if note:
        print(f"[LLM] {note}")


def _start_on_need(address: str) -> bool:
    """Start a writer that is not running, if the operator configured how.

    The wait is capped here on purpose: this predicate is also what a status poll reads, and a poll
    should not hang for the several minutes a cold start can take. The control in Settings and the
    pipeline's own retry both finish the job.
    """

    from services import llm_writer_supervision

    return llm_writer_supervision.start(address, wait_seconds=_ON_NEED_WAIT)


def answers(provider: str, url: str, model_id: str = "") -> bool:
    """Whether the configured writer can be reached right now.

    A provider that is not remote keeps its own answer elsewhere (a subprocess is loaded, or it is
    not). A hosted API is taken at its word: it cannot be verified cheaply and the operator could
    not act on the answer anyway.

    "Reached" includes a server that is up and holding no model: it is a writer that answers, it
    loads itself on the next request, and the wait is announced rather than guessed at.
    """

    global _writer
    _writer = (str(provider or ""), str(url or "").strip())
    if str(provider or "") not in _REMOTE_PROVIDERS:
        return False
    if not str(model_id or "").strip():
        return False
    address = str(url or "").strip()
    if not address or not is_local_endpoint(address):
        return True
    moment = time.monotonic()
    cached = _cache.get(address)
    if cached is not None and moment - cached[0] < _TTL:
        return cached[1]
    try:
        reachable, state = _reachable(address)
    except Exception:
        reachable, state = False, ""  # A server that does not answer is not reachable.
    if not reachable and _start_on_need(address):
        try:
            reachable, state = _reachable(address)
        except Exception:
            reachable, state = False, ""
    if reachable and state == "unloaded":
        _note_released(address)
    _cache[address] = (moment, reachable)
    return reachable


def forget(url: str = "") -> None:
    """Drop the cached answer: for a test, or when the server changed under us."""

    if url:
        _cache.pop(str(url).strip(), None)
    else:
        _cache.clear()


def is_connection_failure(exc: Any) -> bool:
    """Whether this exception means the server was never reached, rather than that it refused."""

    return any(cls.__name__ in _CONNECTION_FAILURES for cls in type(exc).__mro__)


def _missing_writer(address: str) -> str:
    return (
        f"The writer at {address} is not answering. Start that server "
        "(for example Strata) or choose a different writer in Settings -> LLM."
    )


def not_loaded_message(provider: str, url: str) -> str:
    """What to say when there is no writer, in terms the operator can act on.

    A remote writer that is ours and is not answering is not a missing call to load_model: the
    server is stopped, and starting it is the whole fix. Anything else keeps the original wording.
    """

    address = str(url or "").strip()
    if str(provider or "") in _REMOTE_PROVIDERS and address and is_local_endpoint(address):
        return _missing_writer(address)
    return "LLM not loaded. Call load_model() first."


def unreachable_hint(provider: str, url: str, exc: Any) -> str:
    """A sentence for the error the pipeline shows, or "" when it does not apply."""

    if str(provider or "") not in _REMOTE_PROVIDERS or not is_connection_failure(exc):
        return ""
    address = str(url or "").strip()
    if not address or not is_local_endpoint(address):
        return ""
    forget(address)
    return f"\n{_missing_writer(address)}"
