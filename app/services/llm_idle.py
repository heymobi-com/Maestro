"""When the local LLM unloads itself, and who gets to decide.

The idle timer used to be a 60-second constant inside ``llm_service.py``. Measured in use, that
evicted the 26B correction model between two turns of the same conversation -- each correction paid
the reload again -- while the generation paths were already releasing the LLM explicitly whenever
they needed the VRAM. The timeout is therefore a safety net, not a policy: it is generous by
default, a workflow may keep a model warm by naming the seconds it wants, and the environment can
set it once for the machine.

It lives in its own module for the same reason the endpoint helpers do: ``llm_service.py`` is
upstream's file, every line of ours inside it is a line that can conflict, and a policy with a
default, an environment override and a setter is a seam worth paying one import line for instead of
seventeen.
"""

from __future__ import annotations

import os

__all__ = ["DEFAULT_IDLE_SECONDS", "idle_timeout", "set_idle_timeout"]

# A safety net, not a policy: the generation paths release the LLM themselves.
DEFAULT_IDLE_SECONDS: float = 600.0

_IDLE_TIMEOUT_DEFAULT: float = DEFAULT_IDLE_SECONDS
_idle_timeout: float = _IDLE_TIMEOUT_DEFAULT


def set_idle_timeout(seconds) -> float:
    """Set the idle timeout, ignoring anything that is not a positive number.

    Returns the value in force afterwards, so a caller that passed nothing useful learns what the
    policy still is instead of assuming its value took.
    """

    global _idle_timeout
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return _idle_timeout
    if value > 0:
        _idle_timeout = value
    return _idle_timeout


def idle_timeout() -> float:
    """The seconds a loaded model waits before it unloads itself."""

    return _idle_timeout


set_idle_timeout(os.environ.get("MAESTRO_LLM_IDLE_SECONDS", "").strip() or None)
