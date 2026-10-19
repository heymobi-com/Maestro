"""Read the JSON body of a request that may legitimately carry none.

Several Director routes accept an optional body: a Stop click sends one, a plain resume may
not. Spelled inline, the same four lines of guarding (no content-length, malformed JSON, a
list where an object was meant) sat in each route -- in a file upstream also owns, where every
line of ours can conflict on the next update. The rule is ours, so it lives here once.
"""

from __future__ import annotations

from typing import Any

__all__ = ["optional_json_object"]


async def optional_json_object(request: Any) -> dict:
    """The request's JSON object, or ``{}`` when it carries none, or malformed, or not one."""

    if request.headers.get("content-length", "0") == "0":
        return {}
    try:
        parsed = await request.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
