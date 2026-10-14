"""Let a Stop reach the writer, instead of the next batch.

Measured before this existed: of the 109 requests the writer finished, **one** carried
``cancel=True``. ``stop_pipeline`` writes ``cancelled`` and aborts the generation jobs, but a plan in
flight is a blocking HTTP stream that nothing interrupts, so the abandoned generation runs to its last
token. The engine serves requests in order, which is why the writer felt slower *after* a stop: the
next call queues behind work nobody is waiting for.

The plumbing was already there for prompt enhancement. ``studio_enhancement.cancellable_lines`` reads
the socket in a daemon thread, checks cancellation every 100 ms, and its teardown calls
``sock.shutdown(SHUT_RDWR)`` -- so the server sees the client leave and ends its own work. What was
missing is that nothing ever set that context on the Director's planning thread, so
``is_cancellable()`` answered False and every check inside the stream did nothing.

``bind()`` sets it. It reads the pipeline's own status rather than being told: ``stop_pipeline``
already writes ``cancelled``, and a second source of truth would be one more thing to keep in step.

Two properties make the stop take effect without asking the writer twice:

- ``generate_streaming`` checks cancellation *before* it sends anything, so the planner's
  retry-after-failure path raises there instead of issuing a second request to the writer;
- the raise is ``InterruptedError``, which the pipeline thread treats as its own cancellation.
"""

from __future__ import annotations

import threading

from services.studio_enhancement import enhancement_context

__all__ = ["bind", "cancelled", "release"]

# thread ident -> the context entered for it. One per thread: a pipeline thread plans for one pipeline.
_bound: dict[int, object] = {}
_bound_lock = threading.Lock()


def cancelled(pid: str) -> bool:
    """Whether the pipeline this thread is planning was cancelled."""

    try:
        from services.director_pipeline import get_pipeline

        pipeline = get_pipeline(pid) or {}
    except Exception:
        # A lookup that fails is not a Stop: the writer keeps working rather than stopping
        # because the pipeline's own state could not be read.
        return False
    return str(pipeline.get("status") or "") == "cancelled"


def bind(pid: str) -> bool:
    """Make this thread's writer calls cancellable, for as long as the thread lives.

    Entered and deliberately never exited: the thread's whole life is this pipeline, and the context
    resets by token, which a caller with no ``with`` block has nothing to hold. A context is per
    thread, so a second pipeline thread is unaffected. Binding twice on one thread does nothing --
    the first binding already answers for that pipeline.
    """

    ident = threading.get_ident()
    with _bound_lock:
        if ident in _bound:
            return False
        context = enhancement_context({}, lambda: cancelled(pid))
        context.__enter__()
        _bound[ident] = context
    return True


def release() -> None:
    """Undo ``bind`` on this thread: for a test, or for a thread that outlives its pipeline.

    The context re-checks cancellation as it closes, so released *after* a Stop it raises on the way
    out. That check belongs to a caller unwinding its own work; here the binding is being taken back,
    and the context's own ``finally`` has already restored the previous value, so the raise is caught
    and the binding still goes away.
    """

    ident = threading.get_ident()
    with _bound_lock:
        context = _bound.pop(ident, None)
    if context is None:
        return
    try:
        context.__exit__(None, None, None)
    except InterruptedError:
        pass
