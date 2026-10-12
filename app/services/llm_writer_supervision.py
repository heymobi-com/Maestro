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
  endpoint we cannot reach is left alone. **The hand-over is what says so when that happens**:
  ``release_before_render`` names the reason, because a silent release is indistinguishable from a
  render that ran out of memory for no stated reason. Reported from use: a video render failed with
  "CUDA reported an out-of-memory error" while VRAM was free (0.03 GiB allocated, 14.5 GiB free) and
  the GPU idle -- the shortage was host RAM, and the writer's page-locked arena was holding it.
- **It does not fight for the GPU.** The server answers ``busy`` when a request is running or queued,
  and then nothing happens -- which is the right outcome while a planning pass is in flight.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter

__all__ = [
    "autostart",
    "load",
    "memory",
    "release",
    "release_before_render",
    "released_note",
    "router",
    "set_autostart",
    "start",
]

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

# The command that starts a writer which is not running at all, and how long to wait for it. Ours,
# in our own file: it is machine-specific (a path, a launcher) and therefore not something the shared
# application settings should carry.
_CONFIG_NAME = "writer_supervision.json"
_DEFAULT_WAIT = 300.0
_START_TIMEOUT = 900.0

# Guards for the start path: one attempt at a time, and no attempt within this many seconds of the
# last one, so a status poll cannot turn into a stream of launches.
_starting = False
_last_attempt = 0.0
_ATTEMPT_COOLDOWN = 120.0


def _config_path() -> str:
    import os

    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _CONFIG_NAME)


def autostart() -> dict:
    """The command that starts the writer, and how long to wait for it. Empty means never start it."""

    try:
        import json

        with open(_config_path(), "r", encoding="utf-8") as handle:
            saved = json.load(handle) or {}
        return {
            "start": str(saved.get("start") or ""),
            "wait_seconds": float(saved.get("wait_seconds") or _DEFAULT_WAIT),
        }
    except Exception:
        return {"start": "", "wait_seconds": _DEFAULT_WAIT}


def set_autostart(command: str, wait_seconds=None) -> dict:
    """Save the start command. Clearing it means Maestro never starts the writer by itself."""

    import json

    saved = {"start": str(command or "").strip()}
    if wait_seconds:
        try:
            saved["wait_seconds"] = float(wait_seconds)
        except (TypeError, ValueError):
            saved["wait_seconds"] = _DEFAULT_WAIT
    with open(_config_path(), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(saved, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    _forget_notes()
    return autostart()


def _answers(address: str) -> bool:
    """Whether this address answers at all, right now. Cheap: one models request."""

    try:
        import requests

        from services.llm_endpoint import models_url

        return bool(requests.get(models_url(address), timeout=_TIMEOUT).ok)
    except Exception:
        return False


def start(address: str = "", wait_seconds: float = 0.0) -> bool:
    """Run the configured start command and wait for the writer to answer.

    Reported from use: "quiero decir que si no lo arranco primero nunca va a iniciar? aqui es donde
    estoy confundido" -- with the writer stopped, the model list was empty and generating did nothing,
    and nothing said that starting a server was the whole fix. Now Maestro starts it when it needs it,
    from a command the operator controls, and says how long that takes.

    Never raises: a start that cannot happen leaves the honest error the pipeline already shows.
    """

    global _starting, _last_attempt

    address = address or _ours()
    if not address or not _in_application():
        return False
    if _answers(address):
        return True
    setting = autostart()
    if not setting["start"]:
        return False
    moment = time.monotonic()
    if _starting or moment - _last_attempt < _ATTEMPT_COOLDOWN:
        return False  # one attempt at a time, and not one per status poll
    _starting = True
    _last_attempt = moment
    try:
        import subprocess
        import sys

        print(f"[LLM] starting the writer at {address}: {setting['start']}")
        creation = 0
        if sys.platform == "win32":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(setting["start"], shell=True, creationflags=creation)
        limit = float(wait_seconds or 0) or min(float(setting["wait_seconds"]), _START_TIMEOUT)
        limit = min(limit, _START_TIMEOUT)
        waited = 0.0
        while waited < limit:
            time.sleep(3.0)
            waited += 3.0
            if _answers(address):
                print(f"[LLM] the writer at {address} is up after {waited:.0f} s")
                return True
            if int(waited) % 30 == 0:
                print(f"[LLM] still starting the writer at {address} ({waited:.0f} s)")
        print(f"[LLM] the writer at {address} did not come up in {limit:.0f} s")
        return False
    except Exception as exc:  # the honest error the pipeline already shows is the fallback
        print(f"[LLM] could not start the writer at {address}: {exc}")
        return False
    finally:
        _starting = False


@router.get("/api/v1/llm/writer/autostart")
def writer_autostart():
    """What Maestro would run to start the writer, if it is not running."""

    return autostart()


@router.post("/api/v1/llm/writer/autostart")
def writer_set_autostart(body: dict | None = None):
    """Set or clear that command."""

    body = body or {}
    return set_autostart(body.get("start", ""), body.get("wait_seconds"))


@router.post("/api/v1/llm/writer/start")
def writer_start():
    """Start the writer now, and report whether it came up."""

    started = start()
    return {"started": started, **memory()}


def _in_application() -> bool:
    """Whether Maestro itself is running, rather than a test or a one-off import.

    The autostart is an action of the application: it launches a process, and a test suite must never
    launch one. The engine module being imported is what tells the two apart.
    """

    import sys

    return sys.modules.get("wgp") is not None


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
        from services import llm_writer_health

        # A refused connection is the ordinary "it is not running", and the panel should say that
        # rather than show the pool's stack of retries.
        detail = "not running" if llm_writer_health.is_connection_failure(exc) else str(exc)
        return {"state": "unknown", "address": address, "detail": detail}
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


def release_before_render(pid: str = "") -> bool:
    """Hand the writer's memory back before the render stages its checkpoint.

    Called by the queue, because the Director's own stages do not always run on the path a render
    takes: a render started from the film tab never enters that pipeline, so its writer stayed
    resident and the video checkpoint pinned its weights into memory the writer was holding. Nothing
    said the hand-over had not happened, and the render failed out of memory with VRAM free and the
    GPU idle.

    Prints the reason when the writer is not released. The happy path is quiet, because ``release``
    already announced the memory it gave back.
    """

    if release(pid, "render"):
        return True
    answer = memory()
    print(
        f"[Pipeline {pid}] the writer was not released before the render: "
        f"{answer.get('detail') or answer.get('state') or 'no reason reported'}"
    )
    return False
