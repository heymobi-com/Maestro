"""Give an image reference that declares nothing a safe meaning.

Measured on a real project: one reference was a location photograph the user had uploaded
himself, with no intent and no character behind it. The manifest defaults an image's intent to
"identity", so the compiler wrote that photograph into the prompt as a person -- "<Subject 3> is
the supplied image reference, whose facial, bodily, and character identity come from
<Picture 3>" -- and kept it with "fully_preserved" retention. The render then produced a woman
nobody had cast, wearing the wardrobe the prompt described for the actual lead: the face came
from the location photograph and the dress from the text, which is what "a completely different
girl with the same dress" looks like from the user's side.

A bare upload says nothing about what it is for, and the safe reading is the place it shows. A
scene reference makes the render keep the environment; calling it an identity makes the model
invent a person. Only an image with no declared intent and no character behind it is touched: a
library character keeps "identity", and an intent the caller declared is never overridden.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["default_untyped_reference_intents"]

_LOCATION_ROLE = "the environment and location shown in this reference"


def default_untyped_reference_intents(references: Any) -> Any:
    """Submitted references, with an untyped image read as the place it shows."""

    if not isinstance(references, list):
        return references
    normalized: list[Any] = []
    for raw in references:
        if not isinstance(raw, Mapping):
            normalized.append(raw)
            continue
        reference = dict(raw)
        is_image = str(reference.get("type") or "image").strip().lower() == "image"
        declared_intent = str(reference.get("image_intent") or "").strip()
        behind_a_character = bool(
            str(reference.get("library_character_id") or "").strip()
            or str(reference.get("character_name") or "").strip()
        )
        if is_image and not declared_intent and not behind_a_character:
            reference["image_intent"] = "scene"
            if not str(reference.get("role") or "").strip():
                reference["role"] = _LOCATION_ROLE
        normalized.append(reference)
    return normalized
