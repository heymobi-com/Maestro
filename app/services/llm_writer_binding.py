"""Which writer a run is bound to.

A Director run records the writer it was created with -- provider and model -- and every planning
phase re-loads that one, so a later change in Settings does not rewrite a run that is already
underway. The address was the one part of that record nobody kept: `_ensure_llm_loaded` read it from
the current settings instead, which means a run silently follows a later change of address, and if a
different server has moved in, the run is completed by a different writer with the same name.

So the address is recorded with the run the first time a phase needs it, and the run's own reading
wins from then on.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

__all__ = ["remote_url"]


def remote_url(params: MutableMapping[str, Any] | None, services: Mapping[str, Any] | None) -> str:
    """The address this run reaches its writer at, recorded the first time it is needed.

    The record goes into the run's own params, which is the only per-run carrier there is. It lasts
    as long as the run does in this process -- phases, reruns and a queue entry that was frozen with
    it -- and a run that has to be rebuilt from disk after a restart takes the current setting, which
    is exactly what it did before this existed.
    """

    bound = (params or {}).get("llm_remote_url")
    if bound:
        return str(bound)
    current = str((services or {}).get("llm_remote_url") or "")
    if params is not None and current:
        params["llm_remote_url"] = current
    return current
