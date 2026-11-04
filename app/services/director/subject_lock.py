"""The one parser for a brief's SUBJECT LOCK block.

Three readers used to parse this block with three copies of the same regex — the
compiler's lock, the brief validator and the narrator check. They drifted, and each drift
was a real failure:

* ``scene_policy`` matched one parenthetical, so a lock decorated as
  ``<Subject 1> (S1)(Picture 1) is ALWAYS Anita.`` produced no narrator name at all and
  the whole film was planned as off-screen voiceover, with Anita in none of the shots.
* the compiler matched one parenthetical too, so the same brief parsed to an empty lock
  and every voice was attributed by row order.
* ``normalize_speaker_cues`` rewrote *any* parenthetical before "is ALWAYS", so it turned
  ``(Picture 2)`` into ``(S1)`` and destroyed the picture reference.

So the block is parsed here once, tolerantly, and the callers keep only their own meaning
of the result.
"""

import re

#: ``<Subject 1> (S1) (Picture 1) is ALWAYS Anita.`` — any number of parentheticals may
#: sit between the number and "is ALWAYS", and the name may be quoted.
LOCK_RE = re.compile(
    r"<Subject\s*(\d+)>\s*"
    r"((?:\([^)\n]{1,40}\)\s*)*)"
    r"(?:es\s+SIEMPRE|is\s+ALWAYS)\s+"
    r"[<\"'\u201c\u2018*]?"
    # "la Figura del Purgatorio" names the Figure, not "la": the article is not part of
    # the participant, and a lock read as {2: "la"} matches no row at all.
    r"(?:(?:la|el|los|las|the|un|una|a|an)\s+)?"
    r"([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1"
    r"\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+)",
    re.IGNORECASE,
)

_PARENTHETICAL_RE = re.compile(r"\(([^)\n]{1,40})\)")

#: A parenthetical that is trying to be a voice: the canonical "(S1)", the diarizer's
#: "(SPEAKER_00)", or a bare "(S)". Anything else — "(Picture 1)", "(ref 2)" — is
#: decoration and must never be read as, or rewritten into, a cue.
SPEAKERISH_RE = re.compile(r"^\s*(?:S\s*\d*|SPEAKER[_\s]*\d+)\s*$", re.IGNORECASE)

#: The canonical form the compiler reads.
CANONICAL_CUE_RE = re.compile(r"^S[1-9]\d*$", re.IGNORECASE)


def is_canonical_cue(cue: str) -> bool:
    """Whether a cue is in the ``S1`` form the compiler reads."""

    return bool(CANONICAL_CUE_RE.match(str(cue or "").strip()))


def statements(text: str):
    """Yield ``(subject_number, decoration, name)`` for every lock statement."""

    for match in LOCK_RE.finditer(str(text or "")):
        yield int(match.group(1)), match.group(2) or "", match.group(3)


def parse(text: str) -> dict:
    """``{subject_number: name}`` — the Subject-to-participant map."""

    return {number: name for number, _, name in statements(text)}


def decorations(text: str) -> dict:
    """``{subject_number: raw parentheticals}``, for naming a wrong cue in a finding."""

    return {number: decoration for number, decoration, _ in statements(text)}


def speakerish_cues(text: str) -> set:
    """Every voice-ish label the lock states, in whatever spelling it states it."""

    found = set()
    for _, decoration, _ in statements(text):
        for inner in _PARENTHETICAL_RE.findall(decoration):
            if SPEAKERISH_RE.match(inner):
                found.add(inner.strip())
    return found


def cues(text: str) -> dict:
    """``{subject_number: "S1"}`` for the statements that name a voice, normalized."""

    from .cast_vision import canonical_speaker_label

    found = {}
    for number, decoration, _ in statements(text):
        for inner in _PARENTHETICAL_RE.findall(decoration):
            if not SPEAKERISH_RE.match(inner):
                continue
            label = canonical_speaker_label(inner)
            if label:
                found[number] = label
                break
    return found
