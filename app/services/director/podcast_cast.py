"""The closed cast of speakers a podcast plan is written against.

``podcast.py`` is a file upstream also owns, so the resolution below lives here
instead of growing its seam: the planner keeps a one-line call.

The problem it solves is unchanged. The transcript carries raw pyannote ids
(``SPEAKER_00``) while the user's mapping is keyed by stable labels (``(S1)``), so
looking a raw id up in the mapping always missed. The model only ever saw opaque
tokens, invented its own speaker numbering, and 320 of 379 beats came back with no
speaker at all. Resolving the ids up front and injecting the closed vocabulary is
what stopped it.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence


def resolve_cast(
    transcript: Optional[Sequence[Any]],
    speaker_mappings: Optional[Mapping[str, Any]],
) -> tuple[str, Callable[[Any], str]]:
    """Return ``(cast_section, speaker_display)`` for one planning pass.

    ``cast_section`` is the block prepended to the system prompt -- empty when the
    transcript names no one, so a single-speaker project is not told about a cast it
    does not have. ``speaker_display`` renders one transcript speaker the way the
    prompt must state it: the stable label with the name the user gave it.
    """

    from .speaker_binding import (
        SPEAKER_LABEL_RULES,
        cast_vocabulary,
        format_speaker,
        speaker_cast_block,
        speaker_label_order,
    )
    from .voice_gender import declared_gender_for_labels

    speaker_names = {
        sid: info.get("name", sid)
        for sid, info in (speaker_mappings or {}).items()
    }
    raw_to_label = speaker_label_order(transcript)
    vocabulary = cast_vocabulary(speaker_mappings, transcript)
    cast_genders = declared_gender_for_labels(
        "",
        [{"speakerId": label, "name": name} for label, name in vocabulary.items()],
        sorted(vocabulary),
    )
    cast_block = speaker_cast_block(vocabulary, cast_genders)
    cast_section = (
        "CANONICAL SPEAKER CAST — the complete and closed set of speakers "
        f"for this project:\n{cast_block}\n\n{SPEAKER_LABEL_RULES}\n\n"
        if cast_block else ""
    )

    def speaker_display(raw_speaker: Any) -> str:
        label = raw_to_label.get(str(raw_speaker or "").strip().upper(), "")
        if label:
            return format_speaker(label, vocabulary.get(label))
        return str(speaker_names.get(raw_speaker) or raw_speaker or "")

    return cast_section, speaker_display
