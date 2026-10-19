"""The value a model's ``model_mode`` control can actually hold.

``model_mode`` is a model-owned control whose *type* depends on the family that declares it.
YuE2 and the music controls write numbers (``model_mode = 2`` means song rather than
instrumental); the Qwen3 TTS models read the very same field as a **language** -- or, in
CustomVoice, as a **speaker name** -- and call ``.lower()`` on it.

Maestro's Studio keeps one ``params`` object across model switches, and the model-switch
hydration list does not include ``model_mode``, so the control keeps whatever the last model
that owned it wrote. Measured on this machine: YuE2 was generated first in the session, then
the user opened Studio -> Audio -> Speech and tried voice cloning with Qwen3 Base. The request
carried ``model_mode: 2``, and ``(2 or "auto").lower()`` is not a language but an
``AttributeError`` that costs the whole attempt -- eight consecutive attempts failed that way in
about eight seconds each, across Qwen3 Base, Voice Design and CustomVoice.

Nothing was missing from those models: a language was never *sent*. Two halves fix it, and both
live here so the rule is stated once and tested on its own:

* :func:`settings_value` runs where settings are validated, and turns a value the model cannot
  hold into the model's own default -- which is what makes the number a *language* rather than a
  crash, for every path that generates.
* :func:`text_value` runs inside the model, at the line that reads the control, so a non-string
  can never reach ``.lower()`` even from a caller that validated nothing.

The distinction that keeps this safe to run on every model is :func:`is_text_control`: a family
whose choices are numbers (YuE2, Qwen-Image 2.1's inpaint picker, Krea2) is left completely
alone, because for those the number is the correct value.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "LANGUAGE_DEFAULT",
    "declared",
    "describe",
    "is_text_control",
    "settings_value",
    "text_value",
]

# What a Qwen3 TTS language control falls back to: the model detects it from the text, which is
# also the only choice that is correct for a script nobody has labelled.
LANGUAGE_DEFAULT = "auto"


def _choice_values(spec: Any) -> list[Any]:
    """The values of a ``model_modes`` spec, in the several shapes handlers declare it.

    ``choices`` is normally a list of ``[label, value]`` pairs, which is what the model-options
    endpoint and the Gradio dropdown both consume; a plain list of values is accepted too, since
    the value is all this module reads and the label is the interface's business.
    """

    if not isinstance(spec, Mapping):
        return []
    choices = spec.get("choices")
    if isinstance(choices, (str, bytes)) or not isinstance(choices, (list, tuple)):
        return []
    values: list[Any] = []
    for entry in choices:
        if isinstance(entry, (list, tuple)):
            if len(entry) == 2:
                values.append(entry[1])
        elif entry is not None:
            values.append(entry)
    return values


def declared(model_def: Any) -> tuple[list[str], str]:
    """``(choices, default)`` for a control that holds text, or ``([], "")`` for any other.

    A control whose choices are numbers is not this module's business and comes back empty, so
    :func:`settings_value` leaves it alone.
    """

    if not isinstance(model_def, Mapping):
        return [], ""
    spec = model_def.get("model_modes")
    values = _choice_values(spec)
    if not values or not all(isinstance(value, str) for value in values):
        return [], ""
    choices = [value for value in values if value.strip()]
    default = spec.get("default") if isinstance(spec, Mapping) else None
    if not isinstance(default, str) or default not in choices:
        default = choices[0] if choices else ""
    return choices, default


def is_text_control(model_def: Any) -> bool:
    """Whether this model's ``model_mode`` is read as text rather than as a number."""

    return bool(declared(model_def)[0])


def text_value(value: Any) -> str:
    """The value when it can be read as text, otherwise empty so the caller's default applies.

    This is the model-side half: it is called at the line that reads the control, so
    ``(model_mode or "auto").lower()`` is reached with a string, and CustomVoice's
    ``model_mode or supported_speakers[0]`` falls to its own first speaker. It answers one
    question only -- *is this text?* -- because a model being handed a value from a control it
    does not own should degrade to its own default rather than fail the generation.
    """

    return value.strip() if isinstance(value, str) else ""


def settings_value(value: Any, model_def: Any) -> Any:
    """The ``model_mode`` to use for this model: repaired for a text control, otherwise as-is.

    Called where settings are validated, so the whole run -- planning, the queue, the model
    itself -- sees the repaired value rather than only the last reader. A value the model does
    not declare (a number left over from another model, a language it cannot speak, a
    stringified number) becomes the model's own default, and a value it does declare is kept,
    including the spelling the model publishes when the caller used another case.
    """

    choices, default = declared(model_def)
    if not choices:
        return value
    if isinstance(value, str):
        text = value.strip()
        for choice in choices:
            if choice.casefold() == text.casefold():
                return choice
    return default


def describe(model_def: Any) -> str:
    """One line naming what this model accepts, for a log or an error message."""

    choices, default = declared(model_def)
    if not choices:
        return "a number"
    return f"one of {', '.join(choices)} (default {default})"
