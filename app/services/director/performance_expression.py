"""A driving soundtrack is acted, not only synchronized.

H3 has no separate channel for emotion: whatever the face does has to be written
as prose outside ``<d>``. Mouth governance decides *who may sing* -- that is what
``music_performance`` owns -- but a prompt that only says "remains synchronized"
asks for a moving mouth and nothing else, and the render returns a still face.

Measured on a real audio-driven music video in ``app/outputs`` (16 shots): two
visible-expression words in ~70,000 characters, against ~4.7 per shot in the
dialogue-driven projects written by the same planner. The vocabulary below
deliberately excludes lip, mouth, lips, voice and sing, because the
synchronization clause already contains those and counting them would hide the
defect this module exists to catch.
"""

import re
from collections.abc import Mapping

# Written by the compiler for every shot whose performance follows supplied
# audio. Positive and performable, and it defers to the mouth rules instead of
# contradicting them: only *when* the lips move is owned by music_performance.
PERFORMANCE_EXPRESSION_DIRECTION = (
    "Act the performance on the whole face and body, not only the mouth: brow, "
    "eyelids and gaze, jaw and throat tension, breathing, shoulders and weight "
    "carry the audible emotion, while each person's lips still follow only their "
    "own assigned part. A visible performer whose voice is audible never holds a "
    "neutral, unmoving face, and the emotion of the shot is readable in the eyes "
    "and the posture."
)

# Channels a renderer can actually act on. No lip, mouth, voice or singing word
# belongs here: those are the mouth contract's vocabulary, not the face's.
_EXPRESSION = re.compile(
    r"\b(?:expressions?|brows?|eyebrows?|eyelids?|blinks?|blinking|gazes?|"
    r"gazing|eyes?|eye contact|stares?|staring|squints?|winces?|frowns?|"
    r"grins?|smiles?|smiling|smirks?|grimaces?|jaws?|jawline|cheeks?|"
    r"nostrils?|tears?|tearful|throats?|swallows|chin|shoulders?|posture|"
    r"chest|breaths?|breathes|breathing|weight shifts?|trembles?|trembling)\b",
    re.I,
)

# A shot that explicitly asks for stillness or for a person who does not sing is
# not defective: closing the mouth IS the request there, so a neutral face is
# correct and the rule must not fire.
_NON_SINGING = re.compile(
    r"\b(?:non-?singing|does not sing|do not sing|doesn't sing|no singing|"
    r"without lip-?sync\w*|silent presence|"
    r"(?:mouths?|lips?) (?:stay|stays|remain|remains) (?:relaxed and )?closed)\b",
    re.I,
)

_DIALOGUE_BLOCK = re.compile(r"<d>.*?</d>", re.I | re.S)


def _value(reference, key, default=""):
    if isinstance(reference, Mapping):
        return reference.get(key, default)
    return getattr(reference, key, default)


def drives_performance(references) -> bool:
    """True when supplied audio is what the visible performance follows.

    This is the mode the music-video workflow uses when the user brings their own
    song, and it is the one that reaches the compiler without any performance
    direction when there are no dialogue beats.
    """

    for reference in references or ():
        if str(_value(reference, "type", "")).strip().casefold() != "audio":
            continue
        intent = str(
            _value(reference, "audio_intent", "voice") or "voice"
        ).strip().casefold()
        if intent == "drive":
            return True
    return False


def has_visible_subject(subjects) -> bool:
    """A face is only required when a face is on screen.

    A driving-audio shot of an empty room -- "No people are visible" -- asks for
    no performance at all. Measured on shot 0 of a real project, where requiring
    an expression would have refused a prompt that was already correct.
    """

    for subject in subjects or ():
        if isinstance(subject, str):
            if subject.strip():
                return True
            continue
        for key in ("visual_description", "speaker_name", "character_id"):
            if str(_value(subject, key, "") or "").strip():
                return True
    return False


def without_dialogue(text) -> str:
    """The prose outside ``<d>``, which is the only place H3 reads emotion from."""

    return _DIALOGUE_BLOCK.sub(" ", str(text or ""))


def names_visible_expression(text) -> bool:
    """True when the prose names a channel the face or body can be acted on."""

    return bool(_EXPRESSION.search(without_dialogue(text)))


def asks_for_stillness(text) -> bool:
    """True when the shot requests no singing or a closed mouth on purpose."""

    return bool(_NON_SINGING.search(without_dialogue(text)))


def needs_expression(text, *, driving: bool, subjects=()) -> bool:
    """The one place the bounds of this rule live.

    The compiler asks this before adding the direction and the validator asks it
    before refusing a prompt, so there is no path where the two disagree: a
    prompt the compiler builds always satisfies the rule that guards it.

    Four ways to be outside the rule: nothing drives the performance, nobody is
    on screen to act it, the shot already names the face, or the shot asks for
    stillness. Measured on real projects: an empty kitchen in a music video and
    a grandmother explicitly told not to sing are both correct without a word
    about expression.
    """

    if not driving or not has_visible_subject(subjects):
        return False
    body = without_dialogue(text)
    return not (_EXPRESSION.search(body) or _NON_SINGING.search(body))


def performance_expression_problems(
    text, visual="", references=(), subjects=()
) -> list[str]:
    """The contract rule: a driving performance must say how it is acted."""

    if not needs_expression(
        visual or text, driving=drives_performance(references), subjects=subjects
    ):
        return []
    return [
        "the driving audio performance is synchronized but never acted on the "
        "face: name how it shows in the brow, gaze, breath or posture outside <d>"
    ]
