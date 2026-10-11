"""Contradictions a compiled prompt carries into the renderer.

Reported from use: "muchos problemas provienen de contradicciones que se producen desde la planeacion
y generacion de prompts y luego terminan en alucinaciones del checkpoint cuando hay contradicciones
y no sabe que hacer e inventa". A prompt that tells the renderer two different things at once does
not fail: it picks one, averages them, or invents a third reading -- and the run is already paid for
by then.

The audit next door compares each shot against the project's contract (who is who, who speaks,
which sections exist). This one looks for the *inside* problems -- the ones that only show up on
screen -- and several of them are invariants an earlier fix established and nothing has watched
since:

  * a one-shot body that writes another shot's marker at the start of a line (``[Shot 2]``), or that
    keeps a stray ``At MM:SS.mmm`` from the planner's own clock, reads as a cut inside the clip,
    which is how one clip becomes a fast succession of the whole film. The same marker *inside* a
    sentence is the pipeline's own bookkeeping -- ``retention_analysis:`` names the shots a subject
    appears in, and the compile step labels its own shot -- so only line-initial markers count, and
    only when they name a shot other than this clip's own;
  * the brief's own ``CAMERA:`` heading reaching a clip is the measured cause of thirty shots
    performing the same orbit while the plan underneath held eighteen distinct movements;
  * a subject wearing two different outfits in one prompt is the "same dress, different girl" case
    from the reference-intent report;
  * a shot lit by daylight and by night at once, and a ``non_diegetic_music:`` that says silence
    over a scene describing a violin, are the two lighter ones: the renderer resolves them by
    itself, and the shot comes back looking wrong against its neighbours.

Everything here is textual and deliberately conservative: each finding names the two fragments that
disagree, so a warning can be judged (and a hold-then-pan is legitimate, so camera wording is a
warning and not an error). Nothing rewrites a prompt.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from services.director.project_brief import per_shot_headings

__all__ = ["contradiction_findings"]

_SHOT_AT_LINE_START = re.compile(r"(?m)^[ \t]*\[Shot\s*(\d+)\]", re.IGNORECASE)
_LOOSE_TIMESTAMP = re.compile(r"\bAt\s+(?:MM:|\d{1,2}:)\d{2}(?:\.\d{1,3})?\b")
_HEADING_LINE = re.compile(r"(?m)^[ \t]*([^\n:]{2,60}):[ \t]*$")

# Camera wording, in two piles that cannot both be the whole truth. A prompt may honestly say
# "static until the last beat, then a slow pan", so this is a warning that names both halves.
#
# Two rules keep it honest, both measured on 2156 prompts of 41 finished projects: the wording has
# to be about the camera (a "moment of stillness" is a person, and "she tilts her head" is not a
# camera move), so the word must sit in a sentence that also names the camera, the shot or the
# frame; and only *positional* moves conflict with a locked camera -- a static camera can tilt or
# zoom, so those are not listed here.
_STILLNESS = re.compile(
    r"\b(?:static|stationary|locked[- ]off|fixed|stillness|immobile|no camera movement)\b",
    re.IGNORECASE,
)
_MOVEMENT = re.compile(
    r"\b(?:orbit|dolly|crane|tracking|steadicam|handheld|swish|whip|pan|push[- ]in|pull[- ]back)"
    r"(?:s|es|ing|ed)?\b",
    re.IGNORECASE,
)
_CAMERA_ANCHOR = re.compile(r"\b(?:camera|shot|frame|lens|angle)\b", re.IGNORECASE)
_SENTENCE_BREAK = ".!?\n"

_DAY = ("daylight", "sunlight", "daytime", "at noon", "midday", "broad daylight", "morning light")
_NIGHT = ("midnight", "at night", "nighttime", "night scene", "moonlit", "moonlight")

_WARDROBE = re.compile(
    r"(?:wearing|wears|dressed in|wardrobe\s*:)\s*([^.;\n]{3,80})",
    re.IGNORECASE,
)
# Both forms a compiled prompt uses: the field's own "<Subject 2>" and the short "(S2)" tag the
# prose writes beside a name. Measured: without the short tag, a garment worn by the speaker of a
# later paragraph is charged to whoever the last <Subject n> happened to be.
_SUBJECT = re.compile(r"<Subject\s*(\d+)>|\b(?:S|Subject)\s*(\d+)\b", re.IGNORECASE)

# What a phrase actually puts on a body, from a closed list, because two outfits are recognised by
# the garments and not by the words: on 2156 real prompts, comparing the wording flagged "her silky
# red dress and red heels" against "red high heels" as two outfits -- the same dress, the same
# heels, described twice -- and flagged "dark pants, white shirt, and a black leather vest" against
# "working boots" as a second outfit, which is the same outfit's feet. Footwear, hair and
# accessories are deliberately absent for that reason: they complete an outfit, they never
# contradict one.
_BODY_GARMENTS = {
    "dress": ("dress", "dresses", "gown", "gowns", "frock"),
    "suit": ("suit", "suits", "tuxedo", "tux", "smoking"),
    "shirt": ("shirt", "shirts", "blouse", "top", "tops", "t-shirt", "tee"),
    "trousers": ("pants", "trousers", "jeans", "shorts", "skirt", "skirts", "leggings"),
    "coat": ("coat", "coats", "jacket", "jackets", "blazer", "cloak", "cape", "parka", "poncho"),
    "sweater": ("sweater", "sweaters", "jumper", "hoodie", "sweatshirt", "cardigan"),
    "robe": ("robe", "robes", "kimono", "bathrobe", "nightgown", "pajamas", "pyjamas"),
    "uniform": ("uniform", "uniforms", "armor", "armour", "wetsuit", "overalls", "jumpsuit"),
    "vest": ("vest", "vests", "waistcoat"),
    "underwear": ("lingerie", "bikini", "bra", "underwear", "briefs", "boxers"),
}

# What actually conflicts, and what only looks like it should. A whole-body garment against the
# separate pieces that dress a body is the signature of two different people: one subject dressed
# as a woman in a red dress and, in the same prompt, as a man in a shirt, a leather vest and dark
# pants. Everything else is layering -- a sweater under a blazer, a coat over a dress, a suit that
# is itself those pieces -- and a coat or a heel completes an outfit instead of contradicting it.
_WHOLE_GARMENT = frozenset({"dress", "robe", "uniform"})
_SEPARATE_PIECES = frozenset({"shirt", "trousers", "vest", "suit"})

_MUSIC_FIELD = re.compile(
    r"(?mi)^[ \t]*non_diegetic_music\s*:\s*(.{0,120})$",
)
_MUSIC_ABSENT = ("none", "no music", "silence", "silent", "absent", "nothing")
_MUSIC_WORDS = (
    "music", "melody", "song", "guitar", "piano", "drums", "bass line", "orchestra", "synth",
    "violin", "saxophone", "soundtrack", "riff", "percussion", "beat drops",
)
_AUDIO_TAG = re.compile(r"<audio\s*\d+>", re.IGNORECASE)


def _first(values: Iterable[str], text: str) -> str:
    lowered = text.lower()
    return next((value for value in values if value in lowered), "")


def _two_shots(prompt: str, shot: Optional[int]) -> list[dict[str, str]]:
    """A clip that reads as a cut, either by naming another shot or by a timecode inside it."""

    named = sorted({int(value) for value in _SHOT_AT_LINE_START.findall(prompt)})
    other = [number for number in named if shot is None or number != shot]
    stamp = _LOOSE_TIMESTAMP.search(prompt)
    # A timestamp at zero is the start of the clip, not a cut inside it.
    cut = stamp is not None and any(digit != "0" for digit in re.sub(r"\D", "", stamp.group(0)))
    if not other and not cut:
        return []
    what = (
        f"the shot marker(s) {other} written as a new beat"
        if other
        else f"the timestamp {stamp.group(0)!r}"
    )
    # The measurement belongs to the timestamp case: it is the one an earlier fix already strips.
    measured = (
        ""
        if other
        else (
            " Measured on a real 30-shot project: 11 clips carried one such timestamp each, four "
            "seconds into a seven-second clip. The H3 compile step strips a loose marker like this "
            "one, so a compiled prompt that still carries it was written around that step."
        )
    )
    return [{
        "code": "clip-holds-two-shots",
        "severity": "error",
        "message": (
            f"The prompt names more than one shot inside one clip: it carries {what}. "
            "The renderer reads that as a cut inside the clip, which is how a single shot becomes "
            f"a fast succession of everything the film meant to spread out.{measured}"
        ),
    }]


def _brief_heading(prompt: str) -> list[dict[str, str]]:
    leaked = [
        line.group(1).strip()
        for line in _HEADING_LINE.finditer(prompt)
        if line.group(1).strip().upper() in per_shot_headings
    ]
    if not leaked:
        return []
    return [{
        "code": "brief-heading-in-clip",
        "severity": "error",
        "message": (
            f"The prompt still carries the brief's own heading(s) {sorted(set(leaked))}. A per-shot "
            "decision written once in the brief and repeated in every clip overrides the plan "
            "identically everywhere: that is the measured cause of every shot performing the same "
            "camera move while the plan held different ones."
        ),
    }]


def _sentence(prompt: str, start: int, end: int) -> str:
    """The sentence around a match, so a word about a person is not read as one about the camera."""

    stops = [index for index in (prompt.find(mark, end) for mark in _SENTENCE_BREAK) if index >= 0]
    opened = [index for index in (prompt.rfind(mark, 0, start) for mark in _SENTENCE_BREAK) if index >= 0]
    return prompt[(max(opened) + 1) if opened else 0:min(stops) if stops else len(prompt)]


def _about_the_camera(prompt: str, match: re.Match) -> bool:
    return bool(_CAMERA_ANCHOR.search(_sentence(prompt, match.start(), match.end())))


def _camera(prompt: str) -> list[dict[str, str]]:
    still = next(
        (match for match in _STILLNESS.finditer(prompt) if _about_the_camera(prompt, match)),
        None,
    )
    moves = next(
        (match for match in _MOVEMENT.finditer(prompt) if _about_the_camera(prompt, match)),
        None,
    )
    if not still or not moves:
        return []
    return [{
        "code": "camera-contradiction",
        "severity": "warning",
        "message": (
            f"The prompt declares both stillness ({still.group(0)!r}) and movement "
            f"({moves.group(0)!r}). One of "
            "them will be followed and the other ignored. If the camera really holds and then moves, "
            "say so in one clause; if not, keep the one the plan chose."
        ),
    }]


def _lighting(prompt: str) -> list[dict[str, str]]:
    day = _first(_DAY, prompt)
    night = _first(_NIGHT, prompt)
    if not day or not night:
        return []
    return [{
        "code": "lighting-contradiction",
        "severity": "warning",
        "message": (
            f"The prompt describes both daylight ({day!r}) and night ({night!r}). The renderer will "
            "light one of them and the shot will look wrong against its neighbours."
        ),
    }]


def _garment_categories(phrase: str) -> frozenset[str]:
    """The body garments a phrase names, so two outfits are told apart by what is worn."""

    words = set(re.findall(r"[a-z'-]+", phrase.lower()))
    return frozenset(
        category
        for category, names in _BODY_GARMENTS.items()
        if any(name in words for name in names)
    )


def _conflicting_outfits(phrases: set[str]) -> list[str]:
    """The two outfits among these phrases that cannot both be worn, if there are two.

    Only a whole-body garment against separate pieces is reported, because that is the shape of two
    different people being described: one subject in a red dress and, further down the same prompt,
    in a shirt, a leather vest and dark pants. A phrase that names both ("a dress over pants") is
    not a conflict with itself, and layering is not a conflict at all.
    """

    whole = [phrase for phrase in sorted(phrases) if _garment_categories(phrase) & _WHOLE_GARMENT]
    pieces = [phrase for phrase in sorted(phrases) if _garment_categories(phrase) & _SEPARATE_PIECES]
    for dressed in whole:
        for piece in pieces:
            if piece != dressed:
                return [dressed, piece]
    return []


def _garment_phrase(text: str) -> str:
    """The garment itself, without the hair, the pose or the next subject's clothes.

    A captured clause runs to the end of the sentence, which is where "her cream dress, and
    <Subject 2> (S2), in his dark grey suit" would otherwise charge a second person's wardrobe to
    the first one.
    """

    limited = re.split(r"<Subject\s*\d+>|\b[Ss]\s*\d+\b", text, maxsplit=1)[0]
    return re.split(r",\s*(?:with|while|who)\b|\bwhile\b", limited, maxsplit=1)[0].strip()


def _wardrobe(prompt: str) -> list[dict[str, str]]:
    outfits: dict[int, set[str]] = {}
    subject = 0
    for match in re.finditer(rf"{_SUBJECT.pattern}|{_WARDROBE.pattern}", prompt, re.IGNORECASE):
        number = match.group(1) or match.group(2)
        if number:
            subject = int(number)
        elif subject:
            phrase = _garment_phrase(re.sub(r"\s+", " ", match.group(3)))
            if phrase:
                outfits.setdefault(subject, set()).add(phrase.lower())
    mixed = {
        number: _conflicting_outfits(values)
        for number, values in outfits.items()
    }
    mixed = {number: values for number, values in mixed.items() if len(values) > 1}
    if not mixed:
        return []
    detail = "; ".join(f"<Subject {number}>: {values}" for number, values in sorted(mixed.items()))
    return [{
        "code": "wardrobe-contradiction",
        "severity": "error",
        "message": (
            f"A subject is given more than one outfit in the same prompt ({detail}). The wardrobe "
            "comes off one of them and the identity off the reference, which is how a shot ends up "
            "with a different person in the same clothes."
        ),
    }]


def _scene_music_words(prompt: str) -> str:
    """The music words the *scene* uses.

    The music field's own name contains "music", and a ``<Audio n>`` reference line describes the
    style being borrowed from a clip rather than a score over the picture, so both are removed:
    otherwise ``non_diegetic_music: none.`` contradicts itself.
    """

    scene = [
        line
        for line in prompt.splitlines()
        if not line.lstrip().lower().startswith("non_diegetic_music") and not _AUDIO_TAG.search(line)
    ]
    return _first(_MUSIC_WORDS, "\n".join(scene))


def _music(prompt: str) -> list[dict[str, str]]:
    field = _MUSIC_FIELD.search(prompt)
    if not field:
        return []
    declared = field.group(1).strip().lower()
    if not any(word in declared for word in _MUSIC_ABSENT):
        return []
    described = _scene_music_words(prompt)
    if not described:
        return []
    return [{
        "code": "music-contradiction",
        "severity": "warning",
        "message": (
            f"non_diegetic_music says {declared or 'nothing'!r} while the shot describes "
            f"{described!r}. The renderer will score it, and the score will not match what the "
            "scene says is happening."
        ),
    }]


def contradiction_findings(
    prompt: str,
    *,
    draft: str = "",
    shot: Optional[int] = None,
) -> list[dict[str, str]]:
    """Every contradiction found in one compiled prompt, in the audit's own shape.

    ``draft`` is the pre-polish text when it is available; a heading the brief leaked is looked for
    in both, because polishing is exactly the step that can paste new material in. ``shot`` is the
    1-based number of the shot this prompt belongs to, so the prompt calling itself ``[Shot 7]`` is
    not read as a second shot.
    """

    text = str(prompt or "")
    if not text.strip():
        return []
    findings: list[dict[str, str]] = []
    findings.extend(_two_shots(text, shot))
    findings.extend(_brief_heading(text))
    if str(draft or "").strip() and str(draft) != text:
        findings.extend(_brief_heading(str(draft)))
    findings.extend(_camera(text))
    findings.extend(_lighting(text))
    findings.extend(_wardrobe(text))
    findings.extend(_music(text))
    return findings
