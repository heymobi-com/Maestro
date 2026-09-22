"""Live progress and cancellation for an interactive Director planning pass.

A long timeline is planned in batches of about a dozen shots, and the Director
pipeline has always handed the planner a progress callback and a cancellation
callback, so a pipeline run published "Planning audio-film batch 3/5" and stopped
on demand. The one-shot planning endpoint built its planner without either
callback, so the same ten-minute pass was, from the user's side, a silent request
with no progress and no Stop: the only way out was restarting the backend, which
discarded the plan.

This module is the missing registry that both sides meet in:

* The endpoint opens an operation and the planner's callbacks publish into it.
  The events are the planner's own ``_emit_planning_progress`` payloads, so the
  counters shown to the user are the planner's real batch counters rather than a
  second estimate invented here.
* The UI polls :func:`active` for a progress card and calls
  :func:`request_cancel` to stop the pass. The planner checks its cancellation
  callback at every batch boundary and whenever it publishes progress, so a Stop
  lands within one batch instead of never.

Only planning a user triggered through the interactive endpoint is tracked here.
Pipeline planning keeps using its own in-memory pipeline record.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Mapping, Optional


_LOCK = threading.RLock()
_OPERATIONS: dict[str, dict[str, Any]] = {}

# Newest-wins cannot be decided by ``time.time()``: its resolution on Windows is
# around 16 ms, so two passes opened in the same tick compared equal and the card
# reported the older one. A monotonic counter is exact.
_SEQUENCE = 0
_SEQUENCE_LOCK = threading.Lock()


def _next_sequence() -> int:
    global _SEQUENCE
    with _SEQUENCE_LOCK:
        _SEQUENCE += 1
        return _SEQUENCE


def begin(
    kind: str,
    *,
    label: str = "",
    total: int = 0,
) -> str:
    """Register a new interactive planning operation and return its id.

    ``total`` is only a first guess for the card: the planner publishes the real
    counts as soon as its own batching starts, and those events win.
    """

    operation_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _LOCK:
        _OPERATIONS[operation_id] = {
            "id": operation_id,
            "kind": str(kind or "plan"),
            "label": str(label or ""),
            "stage": "starting",
            "message": "Preparing the planning pass...",
            "current": 0,
            "total": max(0, int(total or 0)),
            "started_at": now,
            "updated_at": now,
            "cancelled": False,
            "sequence": _next_sequence(),
        }
    return operation_id


def publish(operation_id: str, event: Mapping[str, Any]) -> None:
    """Record one planner progress event for an operation.

    Missing fields keep their previous value, which is what lets a planner emit
    a short event (for example only a new stage name) without resetting the
    counters the user is reading.
    """

    if not isinstance(event, Mapping):
        return
    with _LOCK:
        operation = _OPERATIONS.get(operation_id)
        if operation is None:
            return
        message = event.get("message")
        if message is not None:
            operation["message"] = str(message)
        stage = event.get("stage")
        if stage is not None:
            operation["stage"] = str(stage)
        for key in ("current", "total"):
            if event.get(key) is None:
                continue
            try:
                operation[key] = max(0, int(event[key]))
            except (TypeError, ValueError):
                continue
        operation["updated_at"] = time.time()


def finish(operation_id: str) -> None:
    """Drop a finished operation so the UI stops showing a progress card."""

    with _LOCK:
        _OPERATIONS.pop(operation_id, None)


def request_cancel(operation_id: str = "") -> bool:
    """Ask an operation to stop, or every live operation when no id is given.

    The planner reads this flag through :func:`is_cancelled` at its next batch
    boundary, so a Stop is honoured between batches rather than mid-batch.
    Returns False when there was nothing left to cancel.
    """

    with _LOCK:
        targets = (
            [operation_id] if operation_id else list(_OPERATIONS)
        )
        cancelled = False
        for target in targets:
            operation = _OPERATIONS.get(target)
            if operation is None:
                continue
            operation["cancelled"] = True
            operation["stage"] = "cancelling"
            operation["message"] = (
                "Stopping after the batch being planned right now..."
            )
            operation["updated_at"] = time.time()
            cancelled = True
        return cancelled


def is_cancelled(operation_id: str) -> bool:
    """Return whether this operation has been asked to stop.

    An operation that is no longer registered (already finished, or a request
    that never opened one) reports False so a planner driven outside the
    endpoint never cancels itself.
    """

    with _LOCK:
        operation = _OPERATIONS.get(operation_id)
        return bool(operation and operation["cancelled"])


def snapshot(operation_id: str) -> Optional[dict[str, Any]]:
    """Return a copy of one operation, or None when it is not running."""

    with _LOCK:
        operation = _OPERATIONS.get(operation_id)
        return _snapshot(operation) if operation else None


def active() -> Optional[dict[str, Any]]:
    """Return the newest running operation, or None when planning is idle."""

    with _LOCK:
        if not _OPERATIONS:
            return None
        newest = max(
            _OPERATIONS.values(),
            key=lambda item: int(item.get("sequence") or 0),
        )
        return _snapshot(newest)


def reset() -> None:
    """Forget every operation. Used by tests to isolate one case from the next."""

    with _LOCK:
        _OPERATIONS.clear()


def _snapshot(operation: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one operation into the payload the UI card renders."""

    now = time.time()
    started_at = float(operation.get("started_at") or now)
    return {
        "id": str(operation.get("id") or ""),
        "kind": str(operation.get("kind") or "plan"),
        "label": str(operation.get("label") or ""),
        "stage": str(operation.get("stage") or ""),
        "message": str(operation.get("message") or ""),
        "current": int(operation.get("current") or 0),
        "total": int(operation.get("total") or 0),
        "cancelling": bool(operation.get("cancelled")),
        "elapsed_seconds": max(0.0, now - started_at),
        "updated_at": float(operation.get("updated_at") or started_at),
    }
