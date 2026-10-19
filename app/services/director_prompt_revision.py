"""One turn of the correction conversation, checked before it reaches the assistant.

The route that serves the correction panel used to spell its four checks inline, which put
them in a file upstream also owns -- every line there is a line that can conflict on the next
update. The checks are ours, so they live here and the route keeps only its docstring.

A turn is refused, not repaired: the director's note is what the rewrite follows, and a turn
that arrives without it, or with a prompt that is not text, has nothing to rewrite. The caller
answers ``400`` with the message returned here.
"""

from __future__ import annotations

from typing import Any

__all__ = ["revision_request_payload"]


def revision_request_payload(body: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate one revision turn. Returns the turn, or the reason it was refused."""

    if not isinstance(body, dict):
        return None, "Request body must be a JSON object"
    instruction = body.get("instruction")
    if not isinstance(instruction, str):
        return None, "instruction must be text"
    current = body.get("prompt")
    if current is not None and not isinstance(current, str):
        return None, "prompt must be text"
    history = body.get("history")
    if history is not None and not isinstance(history, list):
        return None, "history must be a list of turns"
    return {
        "instruction": instruction,
        "prompt": current or "",
        "history": history or [],
    }, ""
