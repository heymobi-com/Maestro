"""Giving the writer's memory back before a render needs it.

Reported from use: "el tema de que strata no haga load and reload automatico con maestro causa ciertos
problemas de coordinacion y ademas confunde al usuario". Two things were true at once -- the writer
holds what the renderer needs, and nobody said so.

Measured on this machine (Strata, qwen3.8-flash-next-iq2_xs, vision on, RTX 5080 / 48 GB):

  loaded          RAM in use 32.7 GiB, VRAM in use 14915 MiB
  after unload    RAM in use 11.9 GiB, VRAM in use  8852 MiB
  load again      22.0 s warm, 52.7 s cold (streaming), and the write that follows is full speed

So the hand-over costs one slow call and buys the render ~20 GiB of RAM and ~6 GiB of VRAM. That is
the strategy this exists for: the writer is the fast brain, the renderer is the muscle, and on 48 GB
they cannot both hold the machine.

Nothing is killed and nothing is restarted. ``POST /v1/unload`` means "give the memory back between
requests", the server keeps listening, and its own ``auto_load`` brings the model back on the next
request -- verified here with a plain chat call and again with a streaming one. The control endpoints
accept JSON from a client that sends no ``Origin`` header, which is exactly what Maestro is, so this
needs no browser and no elevated anything.

Three rules keep it safe:

- **It never raises.** A render must not fail because a convenience could not be arranged; a failure
  here is a line in the log and nothing else.
- **It only speaks to a writer that says it can do this.** A hosted API has no such switch, and an
  endpoint we cannot reach is left alone. In those cases the release is a silent no-op.
- **It does not fight for the GPU.** The server answers ``busy`` when a request is running or queued,
  and then nothing happens -- which is the right outcome while a planning pass is in flight.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter

__all__ = ["load", "memory", "release", "released_note", "router"]

# The control in Settings asks these on the page's behalf: Strata accepts /v1/unload and /v1/load
# only from its own page or a trusted origin, and Maestro's page is neither, while a server-to-server
# call sends no Origin at all.
router = APIRouter()

# address -> monotonic reading of the last "it is released" note, so a status poll does not repeat it
_noted: dict[str, float] = {}
_NOTE_TTL = 120.0

_TIMEOUT = 5.0
# A load waits for the model: measured 22 s warm and 52.7 s cold on this machine, so the ceiling is
# generous -- the point is to answer with the truth, not to give up on the model.
_LOAD_TIMEOUT = 600.0


def _configured() -> tuple[str, str]:
    """The writer named in Settings, for a server that has not planned anything yet.

    Only the configuration of a **running** application counts: the live settings when the engine is
    already imported, and nothing otherwise. Two reasons, both learned the hard way -- importing
    ``wgp`` from here exits the process when a dependency of the engine is missing (correct for the
    application, fatal for anything that merely wants to read the settings), and reading the saved
    file would let a test suite reach a real server and unload the model a director is using.
    """

    try:
        import sys

        running = sys.modules.get("wgp")
        services = (getattr(running, "server_config", None) or {}).get("services", {}) or {}
        return str(services.get("llm_provider", "")), str(services.get("llm_remote_url", "") or "")
    except Exception:
        return "", ""


def _target() -> str:
    """The address whose memory this module may act on, or "" when there is none.

    The address a run used comes first, because that is the one in use. Failing that -- a server that
    has not planned anything yet -- the remote URL named in Settings answers instead, which is what
    the control in Settings is looking at before the first call. The provider field is deliberately
    not consulted: the memory worth releasing is the server's, whether or not the current selection
    happens to be that server, and a server that cannot do this simply does not answer the request.
    """

    from services import llm_writer_health

    recorded = llm_writer_health.last_writer()
    address = recorded[1] or _configured()[1]
    if not address or not llm_writer_health.is_local_endpoint(address):
        return ""
    return address


def _state(url: str) -> str:
    """What the server says about its model: "loaded", "unloaded", or "" when it does not say."""

    import requests

    from services.llm_endpoint import models_url

    response = requests.get(models_url(url), timeout=_TIMEOUT)
    if not response.ok:
        return ""
    try:
        entries = (response.json() or {}).get("data") or []
    except ValueError:
        return ""
    if not entries:
        return "unloaded"  # a server that lists no model while up is holding none
    status = (entries[0] or {}).get("status")
    if isinstance(status, dict):
        return str(status.get("value") or "")
    return "loaded"


def released_note(url: str) -> str:
    """The line to print when a call is about to pay for a released writer, once per spell."""

    moment = time.monotonic()
    last = _noted.get(url)
    if last is not None and moment - last < _NOTE_TTL:
        return ""
    _noted[url] = moment
    return (
        f"{url} released the model to free memory; the next call loads it back "
        "(around 20-50 s on this machine, once)."
    )


def _forget_notes() -> None:
    _noted.clear()


def _ours() -> str:
    """The address this module may act on, or "" when the writer is not one on this machine."""

    return _target()


def memory() -> dict:
    """What the writer is holding right now, for a control that has to show it before it acts."""

    address = _ours()
    if not address:
        return {"state": "unknown", "address": "", "detail": "no writer on this machine is configured"}
    try:
        state = _state(address)
    except Exception as exc:
        return {"state": "unknown", "address": address, "detail": str(exc)}
    return {
        "state": state or "unknown",
        "address": address,
        "detail": "released: the next request loads it back" if state == "unloaded" else "",
    }


def load(pid: str = "") -> bool:
    """Load the writer now, ahead of the request that would otherwise pay for it.

    Used by the control in Settings, where the wait is the point: the director is about to work and
    wants the writer ready rather than slow on the first call.
    """

    try:
        import requests

        from services.llm_endpoint import remote_base

        address = _ours()
        if not address:
            return False
        response = requests.post(
            f"{remote_base(address)}/v1/load",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=_LOAD_TIMEOUT,
        )
        if response.status_code != 200:
            detail = f"HTTP {response.status_code}"
            try:
                detail = (response.json() or {}).get("error", {}).get("message") or detail
            except ValueError:
                pass
            print(f"[LLM] {address} would not load: {detail}")
            return False
    except Exception as exc:  # the control reports this; nothing else depends on it
        print(f"[LLM] {address} would not load: {exc}")
        return False
    _forget_notes()
    print(f"[LLM] {address} loaded on request")
    return True


@router.get("/api/v1/llm/writer/memory")
def writer_memory():
    """What the writer is holding, so the control can show it before it acts."""

    return memory()


@router.post("/api/v1/llm/writer/memory/release")
def writer_release():
    """Give the memory back now, instead of waiting for a render to start."""

    released = release(stage="control in Settings")
    return {"released": released, **memory()}


@router.post("/api/v1/llm/writer/memory/load")
def writer_load():
    """Load the writer now, so the first call of the session is not the slow one."""

    loaded = load()
    return {"loaded": loaded, **memory()}


def _freed_summary(response) -> str:
    """What is still held after a release, from the server's own reading, or ""."""

    try:
        machine = response.json().get("machine") or {}
        ram = (machine.get("ram") or {}).get("used_gib")
        vram = (machine.get("gpu") or {}).get("used_mib")
        if ram is not None and vram is not None:
            return f" ({ram:.1f} GiB of RAM and {vram:.0f} MiB of VRAM still in use)"
    except Exception:
        return ""
    return ""


def release(pid: str = "", stage: str = "") -> bool:
    """Ask the writer to give its memory back, and say what came back.

    Returns whether the writer is (now) released. Everything about this is best-effort: see the
    module docstring for why it must never raise.
    """

    try:
        import requests

        from services.llm_endpoint import remote_base

        address = _ours()
        if not address:
            return False  # nothing on this machine to ask
        state = _state(address)
        if state == "unloaded":
            return True  # already given back; nobody needs to be told
        if state != "loaded":
            return False
        response = requests.post(
            f"{remote_base(address)}/v1/unload",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            detail = f"HTTP {response.status_code}"
            try:
                detail = (response.json() or {}).get("error", {}).get("message") or detail
            except ValueError:
                pass
            print(f"[Pipeline {pid}] writer memory not released: {detail}")
            return False
        print(
            f"[Pipeline {pid}] writer released for the {stage or 'render'}; it loads itself again on "
            f"the next request (~20-50 s, once){_freed_summary(response)}"
        )
    except Exception as exc:  # a render must not fail because this could not be arranged
        print(f"[Pipeline {pid}] writer memory not released: {exc}")
        return False
    _forget_notes()
    return True
