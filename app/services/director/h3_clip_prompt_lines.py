"""How a multi-clip request's prompt text becomes one prompt per clip.

`launch.py` splits a multi-clip request's prompt on the ``---CLIP_BOUNDARY---`` separator that
the Director writes between clips, and falls back to splitting on newlines for the legacy
Studio shape. A single clip carries no separator -- ``separator.join([prompt])`` is just the
prompt -- and a compiled H3 Context-IR prompt is multi-line by design (six labelled fields).
The fallback therefore minted one clip per line, and a one-clip Director resume demanded a
reference manifest for the second of those lines and refused to render:

    ValueError: Missing MiniMax H3 reference manifest for clip 2.

Measured on the real resume: 12 of 13 clips were already rendered, the remaining clip carried
exactly one reference manifest and a six-field prompt, and the job died before any clip ran.
Seven consecutive jobs failed identically (b2465243, 09302e16, 0e7d1703, df66c193, 050e6b9d,
0177ca7b, 21224a30), while the same project's multi-clip batches rendered fine, because two
prompts do produce a separator.

So when a request carries per-clip H3 reference manifests and no separator, the manifests are
the clip count: one manifest is one clip, however many lines its prompt has. Every other shape
keeps the behaviour it had, including the newline fallback the legacy Studio path relies on.
"""

from __future__ import annotations

from typing import Any, Iterable

CLIP_SEPARATOR = "\n---CLIP_BOUNDARY---\n"


def clip_prompt_lines(prompt_text: Any, per_clip_references: Iterable[Any] | None) -> list[str]:
    """The request's prompts, one per clip, for the multi-clip unpacker."""

    text = str(prompt_text or "")
    if CLIP_SEPARATOR in text:
        return [part.strip() for part in text.split(CLIP_SEPARATOR) if part.strip()]
    if per_clip_references is not None and len(list(per_clip_references)) == 1:
        # One manifest is one clip. Splitting this prompt on newlines was the defect.
        return [text.strip()] if text.strip() else []
    return [line.strip() for line in text.split("\n") if line.strip()]
