"""Give a line its speaker's tag when nothing beside it names one.

Reported as a build that stopped before any render with:

    MiniMax H3 Omni would refuse these spoken lines: no cue names the character who speaks them...
    shot 3: The line "[English] Ready to start?" is introduced by "s are deep, chest-based, and
    controlled. Facial expressions show concentration and strain:", which names no character.

The compiled H3 prompt carries the attribution twice, but in two different fields: DIALOGUE AND VOCAL
PERFORMANCE lists "Name (S1) speaks: <words>", while the ``<d>`` blocks are inserted into
``detailed_description``. A cue is read clipped to the field it sits in, so the renderer's check --
which looks *beside the line* -- never sees the first one. Whoever writes that description therefore
decides whether the shot can be rendered at all: a planner held to a grammar ends it by naming the
speaker, and one free of a grammar ends it with the voice itself, which is how a run of multi-voice
shots became unrenderable.

So the tag goes where the check looks, and **only where the check refuses the line**. The judgement is
the renderer's own function rather than a copy of its rules: a second implementation would drift, and
a copy that accepted a line the renderer refuses would leave the same broken build behind. It also
keeps an already-repaired prompt identical when it is compiled again, because a line whose cue names a
speaker never trips the check.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["compile_blocks", "spoken_sentence", "subject_tag"]

# The prefixes this module adds, so a later compile starts from the text it started from last time.
# The compiler's own strip removes only the <d>...</d> span, so without this the first pass's tag
# would be left stranded in the prose and the lines would be judged against it.
_OWN_PREFIX_RE = re.compile(r"<Subject\s*\d+>\s*says:\s*", re.IGNORECASE)

# The window the renderer's check reads before a line: what has to fit between the speaker's name and
# the line is bounded by it.
CUE_WINDOW = 160
CUE_MARGIN = 12


def _h3():
    """The compiler's own helpers, imported late: the compiler calls this module back."""

    from services.director import h3_dialogue

    return h3_dialogue


def spoken_sentence(beat: Mapping[str, Any]) -> str:
    """One beat as the compiler states it: who speaks, how, and the exact tagged line.

    The line has to name its speaker **beside itself**, and the description the planner put between
    the two is unbounded. Measured: with a 251-character delivery the check reported "no cue names
    the character who speaks them" for a sentence that opens with "Valeria (S1) speaks" -- the
    attribution was right there, just further than the 160 characters the check reads back. A writer
    free of a grammar produces exactly that shape, so the delivery is bounded to what fits instead of
    being trusted. A delivery that already fits is returned untouched.
    """

    h3_dialogue = _h3()
    name = h3_dialogue._normalized_space(beat.get("speaker_name", ""))
    name_text = f"{name} " if name else ""
    # The label is rendered once, by Maestro: a planner-authored label left in the name produced
    # "(S1) (S1) speaks ...".
    prefix = f"{name_text}{beat.get('stable_id', '')} speaks"
    delivery = h3_dialogue._compact_h3_line_delivery(beat.get("delivery", ""))
    # What has to fit before the line is the prefix, the delivery and the ": " that closes it.
    room = CUE_WINDOW - len(prefix) - len(": ") - CUE_MARGIN
    delivery = _bounded(delivery, room)
    delivery_text = f" {delivery}" if delivery else ""
    return f"{prefix}{delivery_text}: {beat['tag']}."


def _bounded(delivery: str, room: int) -> str:
    """A delivery short enough that the speaker stays beside the line."""

    if room <= 0:
        return ""
    text = str(delivery or "").strip()
    if len(text) <= room:
        return text
    cut = text[:room].rstrip()
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;.:-")


def subject_tag(speakers: Mapping[str, Any], beat: Any) -> str:
    """The ``<Subject N>`` tag of the character a beat's line belongs to.

    Falls back to the first subject when the planner named nobody, which is the same guess the
    spoken-lines instruction list already makes -- better a best-effort tag than a shot the renderer
    refuses outright.
    """

    h3_dialogue = _h3()
    speaker_id = h3_dialogue._normalized_space(h3_dialogue._field(beat, "speaker_id", ""))
    stable_id = ""
    for key in (speaker_id, speaker_id.casefold()):
        if key in speakers:
            stable_id = speakers[key][0]
            break
    if not stable_id:
        stable_id = speaker_id or "(S1)"
    match = h3_dialogue.re.fullmatch(r"\(S(\d+)\)", stable_id or "")
    return f"<Subject {match.group(1)}>" if match else ""


def compile_blocks(
    prompt: str,
    dialogue_beats: Sequence[Any],
    subjects: Iterable[Any] | None,
    default_language: str = "English",
) -> tuple[str, list[str]]:
    """The text to compile -- without this module's own tags -- and the blocks for these beats.

    Returning the text as well is deliberate: the caller inserts the blocks itself, and a caller that
    inserted them into a text that still carried last pass's prefixes would judge every line against
    a tag that no longer sits beside it.
    """

    h3_dialogue = _h3()
    speakers = h3_dialogue._speaker_map(subjects or [])
    cleaned = _OWN_PREFIX_RE.sub("", str(prompt or "")).strip()
    blocks: list[str] = []
    # A line's cue is what precedes it, so each block is judged on the text the one before leaves.
    text = cleaned
    refusals = len(h3_dialogue.h3_unresolved_speaker_cue_problems(text))
    for beat in dialogue_beats or []:
        words = h3_dialogue._field(beat, "spoken_text", "")
        if not h3_dialogue._normalized_space(words):
            continue
        block = h3_dialogue.h3_dialogue_tag(words, default_language)
        candidate = f"{text} {block}".strip()
        if len(h3_dialogue.h3_unresolved_speaker_cue_problems(candidate)) > refusals:
            tag = subject_tag(speakers, beat)
            if tag:
                block = f"{tag} says: {block}"
                candidate = f"{text} {block}".strip()
        refusals = len(h3_dialogue.h3_unresolved_speaker_cue_problems(candidate))
        blocks.append(block)
        text = candidate
    return cleaned, blocks
