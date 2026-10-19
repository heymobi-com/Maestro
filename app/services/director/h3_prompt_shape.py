"""What a refused H3 prompt is made of, in one line the log can carry.

A refusal like `detailed_description is missing [Shot 1]` says what the contract wanted but not
what it was given, so the only way to tell a truncated field from an empty one was to reproduce
the whole planning pass. This prints the shape instead: the fields that were found, how long each
one is, and where the body actually starts.

Measured cause of the refusal this was written for: `_extract_h3_fields` searched for a field
label without anchoring it to a line start, so a label named in the prose before the real field
won the match and the slice ended at the real label. The validator counted one field; the reader
read a different, shorter one; and a prompt that carries `[Shot 1]` was refused for missing it.
"""

from __future__ import annotations

__all__ = ["describe"]


def describe(fields: dict[str, str]) -> str:
    """One line naming the fields a refused prompt was found to contain."""

    if not fields:
        return "[MiniMax H3] Refused a prompt that declares no Context-IR field at all."
    shapes = ", ".join(f"{name}={len(value)}c" for name, value in fields.items())
    body = str(fields.get("detailed_description") or fields.get("integrated_multimodal_description") or "")
    head = body[:120].replace("\n", " ")
    return (
        f"[MiniMax H3] Refused a prompt built from these fields: {shapes}. "
        f"Body starts: {head!r}"
    )
