"""Turn a freeform brief into the shape the pipeline reads, and say what was wrong.

A brief is the one place a person writes libre prose, and it is also the input the rest
of the pipeline reads structurally: the SUBJECT LOCK numbers the participants, the
`Stage N` beats decide which stage a clip belongs to, the camera language reaches the
planner once per film, and a character named in the cast list is a character the model
will draw. Every measured failure in this codebase traces back to that mismatch:

* "Abuela Rosa is ALWAYS Abuela Rosa" parsed to no lock at all, so the Subjects were
  numbered from the planner's own list;
* a `DESCRIPTION` written as one paragraph gave every clip no story position, so
  characters appeared and disappeared between shots;
* `push-ins whenever she is watched` produced `medium shot + push in` on every clip of
  the climax;
* the whole brief travelled into every prompt, so the priest's wardrobe was described
  to a shot about a village street.

Patching the code for one film at a time does not scale, and the four workflows disagree
about what a good brief even is: a podcast wants one studio and no stages, a short film
wants stages and places, a music video wants sections, a viral clip wants a hook. So the
contract is stated once, per workflow, in `scene_policy`, and this module checks a brief
against it and rewrites it into that shape.

Two halves, and the deterministic one is the important one:

``diagnose``  — no model. Names what is missing or ambiguous, in the terms the code uses.
``structure`` — hands the brief and the diagnosis to the local LLM, which may only
                reorganise and fill gaps; the original words and ideas are kept.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from .scene_policy import (
    EVENT_ACTION_RE,
    MUSIC_VIDEO,
    PLACE_WORDS,
    PODCAST,
    SHORT_FILM_AUDIO,
    SHORT_FILM_STORY,
    VIRAL_VIDEO,
    beat_states_an_event,
    scene_policy,
)

__all__ = [
    "CANONICAL_SECTIONS",
    "consolidate_cast_rows",
    "derive_subject_lock",
    "description_states_events",
    "diagnose_brief",
    "normalize_speaker_cues",
    "normalized_brief",
    "strip_template_placeholders",
    "structure_prompt",
]

#: The sections the canonical brief has, in the order they are written.
CANONICAL_SECTIONS = (
    "SUBJECT LOCK",
    "CHARACTERS",
    "PRESENCES",
    "CAMERA",
    "AMBIENCE",
    "DESCRIPTION",
    "NEGATIVE",
)

#: Words that make a camera or stillness instruction a rule for every shot.
_GLOBAL_RULE_WORDS = ("whenever", "always", "every time", "throughout", "constant", "non-stop")

#: Words that name a place, used to detect a stage that wanders.
_PLACE_WORDS = PLACE_WORDS

_CAST_ROW_RE = re.compile(
    r"(?im)^[ \t]*(?:S(\d)\s*)?[\(\[]?([A-ZÁÉÍÓÚÜÑ][^:\n]{2,60}?)[\)\]]?\s*:\s*\S"
)
_STAGE_RE = re.compile(
    r"(?i)\b(?:stage|beat|shot|scene)\s+(\d+)\b|^\s*(?:scene|beat|section)\s+(\d+)\b",
    re.MULTILINE,
)

# A line whose key is in angle brackets is a template placeholder, not content.
# Measured on a real brief the assistant produced: the writer copied
# "<every other character>: named as presences and objects" straight out of the
# instruction, and the planner then read a cast row with no character in it. The
# heading "PRESENCES (only what this workflow needs):" leaked the same way.
_PLACEHOLDER_LINE_RE = re.compile(r"^\s*<[^>\n]{1,60}>\s*:")
# A value the prompt used as a placeholder, copied back as if it were content:
# "REFERENCES:\n(none supplied)". Measured on a real proposal.
_PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)^\s*[\(\[]?\s*"
    r"(?:none supplied|not supplied|no references?|no characters?|none|n/?a|unknown)"
    r"\s*[\)\]]?\s*\.?\s*$"
)
_INSTRUCTION_HEADING_RE = re.compile(r"\s*\((?:only )?what (?:this workflow|the workflow)[^)]*\)")

# The lock with its cue kept raw, so a wrong cue can be named in a finding rather than
# silently dropped. Mirrors `h3_dialogue._SUBJECT_LOCK_RE`: any number of parentheticals
# may sit between the number and "is ALWAYS", because a model decorates the line.
# Measured: "<Subject 1> (S1)(Picture 1) is ALWAYS Anita." used to match no lock at all.
_SUBJECT_LOCK_RAW_RE = re.compile(
    r"<Subject\s*(\d+)>\s*((?:\([^)\n]{1,40}\)\s*)*)(?:es\s+SIEMPRE|is\s+ALWAYS)",
    re.IGNORECASE,
)

_PARENTHETICAL_RE = re.compile(r"\(([^)\n]{1,40})\)")

# A parenthetical that is trying to be a voice: the canonical "(S1)", the diarizer's
# "(SPEAKER_00)", or a bare "(S)". Anything else -- "(Picture 1)", "(ref 2)" -- is
# decoration and must not be reported as a wrong cue.
_SPEAKERISH_RE = re.compile(r"^\s*(?:S\s*\d*|SPEAKER[_\s]*\d+)\s*$", re.IGNORECASE)


def _raw_lock_cues(text: str) -> set[str]:
    """The speaker-ish labels the lock states, in whatever spelling."""

    cues: set[str] = set()
    for match in _SUBJECT_LOCK_RAW_RE.finditer(str(text or "")):
        for inner in _PARENTHETICAL_RE.findall(match.group(2) or ""):
            if _SPEAKERISH_RE.match(inner):
                cues.add(inner.strip())
    return cues


def _is_canonical_cue(cue: str) -> bool:
    """Whether a cue is in the ``S1`` form the compiler reads."""

    return bool(re.fullmatch(r"S\s*[1-9]\d*", str(cue or "").strip(), re.IGNORECASE))

# A stage is concrete when somebody does something in it. A beat that only describes
# the format -- "epic scenes illustrate what is being narrated" -- leaves the planner
# to invent the whole film from it, which is the wandering the user reported.
_CONCRETE_ACTION_RE = EVENT_ACTION_RE


# A presence row that names what someone looks like is an appearance; one that says when
# they turn up ("presence in the biblical action stages") is not, and each clip then
# invents a different face for them.
_APPEARANCE_RE = re.compile(
    r"\b(?:[a-z]+-)?(?:haired|bearded|eyed|skinned)\b|"
    r"\b(?:black|white|grey|gray|brown|blonde|blond|red|auburn|dark|pale|olive|tan|"
    r"bronze|gold|golden|silver|iron|steel|copper|blue|green|ochre|cream|red)\b|"
    r"\b(?:hair|beard|moustache|mustache|eyes?|face|skin|wrinkles?|scars?|tall|short|"
    r"thin|gaunt|stoop(?:ed)?|heavy|lean|muscular|young|old|elderly|teenage|youthful)\b|"
    r"\b(?:tunic|robe|robes|cloak|coat|dress|gown|skirt|apron|trousers|pants|shirt|"
    r"blazer|jacket|suit|almond|alb|chasuble|stole|armour|armor|helmet|greaves|shroud|"
    r"veil|scarf|headscarf|hat|cap|slippers?|boots?|shoes?|sandals?|gloves?|belt|sash|"
    r"rope|linen|wool|leather|silk|canvas|chainmail|scale|spear|shield|sling|necklace|"
    r"rosary|earrings?|spectacles|glasses|giants?|dwarfs?|soldiers?|shepherds?|kings?|"
    r"queens?|priests?|monks?|child(?:ren)?|elders?|crowds?|women|woman|men|man|"
    r"boys?|girls?|figures?)\b|"
    r"\b(?:about|around|aged?)\s+\d{1,3}\b|\b\d{1,3}\s*(?:years? old|-year-old)\b",
    re.I,
)


def _describes_appearance(text: str) -> bool:
    """True when a cast row says what someone looks like, not merely when they appear."""

    return bool(_APPEARANCE_RE.search(text))


def description_states_events(brief: str) -> bool:
    """True when a brief's beats are events rather than a description of the format.

    Two briefs measured on this codebase, and the difference is not length:

        "Dawn-grey kitchen: Rosa before a wooden table. Cobbled street: children in
         paper masks. … Small village church: candles lit, Rosa kneels."

        "Anita narrates the biblical story in a photorealistic documentary in which
         Anita moves immersively through the settings. The documentary presents epic
         biblical scenes that visually illustrate what is being narrated."

    The first names places the film goes to; the second names 'the settings', 'the
    events' and 'the audience'. So a description is a shot list when it visits at least
    two places, or when most of its beats state a performed action. Neither test alone
    is enough: a prose brief is often mostly tableaux, and a contract can contain one
    stray verb.
    """

    stages = _stage_texts(str(brief or ""))
    if not stages:
        return False
    # An explicit "Stage N" is the person asking for a sequence, whatever the words are.
    if re.search(r"(?i)\bstage\s+\d", _description_prose(brief)):
        return True
    bodies = [body for _number, body in stages]
    places = {
        word for body in bodies for word in PLACE_WORDS
        if re.search(rf"\b{word}\b", body, re.I)
    }
    # Two different places the film goes to is a shot list. A contract says "the
    # settings", "the spaces", "the world" — those are not places.
    if len(places) >= 2:
        return True
    # One place plus performed actions is also a shot list ("the kitchen: she lifts the
    # cup"). One place and no action is a tableau, and a narration is a better plan.
    if len(places) == 1:
        events = sum(1 for body in bodies if beat_states_an_event(body))
        return events * 2 >= len(bodies)
    return False


def normalize_speaker_cues(
    brief: str,
    transcript_speakers: Optional[Iterable[Any]] = None,
) -> str:
    """Rewrite a raw diarization id in a cue to the ``(S1)`` label the compiler reads.

    A model given ``("SPEAKER_00",)`` wrote ``<Subject 1> (SPEAKER_00) is ALWAYS Anita.``
    verbatim. That cue is not a number, so the lock regex matched nothing, the lock was
    discarded, and the plan then inferred every voice from row order.
    """

    from .cast_vision import canonical_speaker_labels

    labels = canonical_speaker_labels(transcript_speakers or ())
    fallback = labels[0] if labels else "S1"

    def replace(match: re.Match) -> str:
        cue = match.group(1).strip()
        if _is_canonical_cue(cue):
            return f"({cue})"
        # "(Picture 1) is ALWAYS Anita." also ends in a parenthetical before "is ALWAYS".
        # Rewriting it turned a picture reference into a speaker cue — and then the
        # picture the narrator was supposed to come from was gone.
        if not _SPEAKERISH_RE.match(cue):
            return match.group(0)
        number = re.search(r"(\d+)", cue)
        if number:
            index = int(number.group(1))
            if index < len(labels):
                return f"({labels[index]})"
            if index == 0 and labels:
                return f"({labels[0]})"
        return f"({fallback})"

    return re.sub(r"\(\s*([^()\n]{1,40})\s*\)(?=\s*(?:is\s+ALWAYS|es\s+SIEMPRE))", replace, brief, flags=re.I)


# Capitalized words that open a sentence or describe the film rather than name a person.
#: The pipeline's own vocabulary. A brief can quote it back — the planner's guidance talks
#: about a shot, a stage, a clip, a beat — and a validator that reads those as characters
#: invents cast out of its own instructions: measured on a real run, "Beat" was reported as
#: "appears in the story but has no entry in CHARACTERS".
_PIPELINE_VOCAB = {
    "beat", "beats", "stage", "stages", "shot", "shots", "clip", "clips", "scene",
    "scenes", "frame", "frames", "act", "acts", "section", "sections", "part", "parts",
    "take", "takes", "cut", "cuts", "panel", "panels", "storyboard", "timeline",
    "sequence", "prompt", "prompts", "reference", "references", "character",
    "characters", "cast", "presence", "presences", "subject", "subjects", "camera",
    "lens", "palette", "lighting", "ambience", "audio", "voice", "voices", "dialogue",
    "subtitle", "subtitles", "caption", "captions", "style", "mood", "tone", "note",
    "notes", "rule", "rules", "guide", "guides", "brief", "project", "workflow",
    "director", "planner", "plan", "planning", "model", "render", "renderer",
}

_NOT_A_NAME = _PIPELINE_VOCAB | {
    "this", "the", "a", "an", "no", "not", "only", "every", "each", "all", "any",
    "narrator", "documentary", "film", "story", "biblical", "ancient", "figure",
    "figures", "people", "soldiers", "world", "audience", "viewer", "sound", "light",
    "chapter", "title", "standing", "then", "when", "while", "after", "before",
    "because", "and", "but", "so", "if", "once", "still", "here", "there", "they",
    "she", "he", "it", "we", "you", "i", "his", "her", "their", "one", "two", "three",
}


def derive_subject_lock(
    brief: str,
    transcript_speakers: Optional[Iterable[Any]] = None,
    references: Optional[Iterable[Any]] = None,
) -> str:
    """Write the ``SUBJECT LOCK`` a prose brief never had, when the answer is unambiguous.

    A brief written as prose has no lock, so the compiler numbers the participants from
    the planner's own list — the mistake that puts one character's voice on another. The
    model usually fixes that, but the model is not always available or correct, and the
    mechanical pass is what the person falls back to.

    It only acts when nothing has to be guessed: exactly one voice on the audio, and
    exactly one name the brief presents as the speaker (a name near "narrates", "speaks",
    "voice", "host", "tells"), or a single-name cast. Anything else is left to the model,
    because a wrong lock is worse than none.
    """

    from .cast_vision import canonical_speaker_labels

    text = str(brief or "")
    from .h3_dialogue import project_subject_lock

    if project_subject_lock(text):
        return text
    labels = canonical_speaker_labels(transcript_speakers or ())
    if len(labels) != 1:
        return text
    label = labels[0]

    names = list(_cast_rows(text))
    if not names:
        names = [item["name"] for item in _roster_names(references)]
    if not names:
        # A prose brief casts nobody explicitly; the names it uses are its cast.
        seen: set[str] = set()
        for match in re.finditer(
            r"\b([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})?)", text
        ):
            candidate = match.group(1)
            key = candidate.casefold()
            if key in seen:
                continue
            if key in _NOT_A_NAME or key.split()[0] in _NOT_A_NAME:
                continue
            if key.split()[0] in _PLACE_WORDS:
                continue
            seen.add(key)
            names.append(candidate)
    # Score, rather than flag, because "narrates the story of David" also puts a name
    # close to a speaking verb. A name the sentence itself calls the speaker wins over a
    # name the narration merely mentions.
    scored: list[tuple[int, str]] = []
    for name in names:
        score = 0
        if re.search(
            rf"\b{re.escape(name)}\b[^.!\n]{{0,60}}\b"
            r"(narrat\w*|speak\w*|says|telling|tells|voice|host\w*|present\w*)\b",
            text, re.I,
        ):
            score += 3
        if re.search(
            rf"\b(narrat\w*|speak\w*|voice|host\w*|present\w*|tells?)\b"
            rf"[^.!\n]{{0,28}}\b{re.escape(name)}\b",
            text, re.I,
        ):
            score += 2
        if score:
            scored.append((score, name))
    if not scored:
        if len(names) != 1:
            return text
        narrator = names[0]
    else:
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return text  # two plausible narrators is not unambiguous
        narrator = scored[0][1]

    lock = f"SUBJECT LOCK:\n<Subject 1> ({label}) is ALWAYS {narrator}.\n"
    rest = "\n".join(
        line for line in text.splitlines()
        if not re.match(r"(?i)^\s*subject\s+lock\s*:", line)
    ).lstrip()
    return f"{lock}\n{rest}" if rest else lock


def _roster_names(references: Optional[Iterable[Any]]) -> list[dict]:
    if not references:
        return []
    from .cast_vision import reference_roster

    return reference_roster(references)


def strip_template_placeholders(brief: str) -> str:
    """Remove the assistant's own template lines from a brief.

    Two shapes leaked from the structuring instructions: a line whose key is an
    angle-bracket placeholder, and a heading carrying the instruction in parentheses.
    Both are the machine talking to itself, and both reach the planner as content.
    """

    kept: list[str] = []
    for line in str(brief or "").splitlines():
        if _PLACEHOLDER_LINE_RE.match(line):
            continue
        if _PLACEHOLDER_VALUE_RE.match(line):
            continue
        cleaned = _INSTRUCTION_HEADING_RE.sub("", line)
        if cleaned.strip() or not line.strip():
            kept.append(cleaned)
    return "\n".join(_drop_empty_sections(kept))


def _drop_empty_sections(lines: list[str]) -> list[str]:
    """Remove a heading that has no content of its own.

    ``REFERENCES:`` followed by ``(none supplied)`` was the leak; once the placeholder is
    gone the heading is left standing over nothing. Only a heading whose next non-blank
    line is another heading (or the end) is dropped -- a heading followed by prose is the
    normal shape of a brief.
    """

    heading = re.compile(r"^[ \t]*[A-Z][A-Z0-9 /_\-()]{2,50}\s*:\s*$")
    kept: list[str] = []
    for index, line in enumerate(lines):
        if heading.match(line):
            following = next(
                (item for item in lines[index + 1:] if item.strip()), ""
            )
            if not following or heading.match(following):
                continue
        kept.append(line)
    return kept


def _sections(brief: str) -> dict[str, str]:
    """``{"SUBJECT LOCK": "...", ...}`` for the headed sections of a brief."""

    found: dict[str, str] = {}
    for name, body in _section_order(brief):
        found.setdefault(name, body)
    return found


#: A cast row inside a section body: ``Name (S1):`` or ``The Israelite Soldiers:``.
#: Rows are read from the body rather than line by line because a brief arrives as one
#: long line (measured: 5,391 characters, zero newlines) and then only the first row of
#: CHARACTERS was ever seen — David and Goliath were dropped from the cast, and the model
#: had no appearance for either of them.
_CAST_ROW_SPLIT_RE = re.compile(
    r"(?:\A|\n|(?<=[.;])\s)\s*"
    r"([A-Z][A-Za-z\u00c0-\u024f\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1"
    r"0-9 .'\-()]{0,48}?)\s*:\s*"
)

#: Words that may sit inside a name without being capitalised.
_NAME_CONNECTORS = {"of", "the", "de", "del", "of", "and", "la", "las", "los", "el", "du"}


def _looks_like_a_row_label(label: str) -> bool:
    words = label.split()
    if not words or len(words) > 6:
        return False
    if not any(len(word) > 1 and word[0].isalpha() for word in words):
        return False
    return all(
        word[0].isupper()
        or word.casefold() in _NAME_CONNECTORS
        or (word.startswith("(") and word.endswith(")"))
        for word in words
    )


def _section_rows(body: str) -> list[tuple[str, str]]:
    """``[(name, description), ...]`` for the rows a section body writes inline."""

    text = str(body or "")
    matches = [
        match for match in _CAST_ROW_SPLIT_RE.finditer(text)
        if _looks_like_a_row_label(" ".join(match.group(1).split()))
    ]
    rows: list[tuple[str, str]] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        label = " ".join(match.group(1).split())
        rows.append((label, " ".join(text[match.end():end].split())))
    return rows


def _cast_rows(brief: str) -> dict[str, str]:
    """``{"Abuela Rosa": "<its description>", ...}`` from CHARACTERS / PRESENCES."""

    rows: dict[str, str] = {}
    sections = _sections(brief)
    for name in ("CHARACTERS", "PRESENCES", "CAST", "CHARACTER PROFILES"):
        for section_name, text in sections.items():
            if not section_name.startswith(name.split()[0]):
                continue
            for label, body in _section_rows(text):
                rows.setdefault(label, body)
    return rows


def consolidate_cast_rows(brief: str) -> str:
    """One row per character, keeping every fact both rows carried.

    A structured brief routinely lists the same person twice — ``David`` in CHARACTERS
    with an appearance read from his picture, and ``David`` again in PRESENCES with "does
    not speak". The row in PRESENCES has no appearance, so it read as a character the
    brief never describes, and a name spelled ``Goliath`` in one section and ``Goliat``
    in the other read as two people.

    The two rows are merged into one in CHARACTERS: the appearance first, then whatever
    the presence row said that the character row did not. Only the duplicate changes.
    """

    text = str(brief or "")
    sections = _sections(text)
    characters = ""
    presences = ""
    for name, body in sections.items():
        if name.startswith("CHARACTER"):
            characters = body
        elif name.startswith("PRESENCE"):
            presences = body
    if not characters or not presences:
        return text

    from .cast_vision import same_name

    def rows(body: str) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for line in body.splitlines():
            match = re.match(r"^\s*(?:[-*]\s*)?([^:\n]{2,50}?)\s*:\s*(.+)$", line)
            if match:
                found.append((" ".join(match.group(1).split()), " ".join(match.group(2).split())))
        return found

    character_rows = rows(characters)
    presence_rows = rows(presences)
    kept_presence: list[tuple[str, str]] = []
    merged = list(character_rows)
    for presence_name, presence_text in presence_rows:
        target = next(
            (index for index, (name, _body) in enumerate(merged) if same_name(name, presence_name)),
            None,
        )
        if target is None:
            kept_presence.append((presence_name, presence_text))
            continue
        name, body = merged[target]
        if presence_text.casefold() in body.casefold():
            continue
        body = body.rstrip()
        if body.endswith("."):
            body = body[:-1]
        separator = " " if body.endswith((";", ",", ":")) else "; "
        merged[target] = (name, f"{body}{separator}{presence_text}")

    def render(items: list[tuple[str, str]]) -> str:
        return "\n".join(f"{name}: {body}" for name, body in items)

    # Rebuilt section by section so nothing else in the brief moves: this pass may only
    # change the two cast sections.
    by_name = {name: body for name, body in _sections(text).items()}
    for name in list(by_name):
        if name.startswith("CHARACTER"):
            by_name[name] = render(merged)
        elif name.startswith("PRESENCE"):
            by_name[name] = render(kept_presence)
    rebuilt: list[str] = []
    for name, body in _section_order(text):
        if by_name.get(name, "").strip():
            rebuilt.append(f"{name}:\n{by_name[name].strip()}")
    for name, body in by_name.items():  # anything the order walk missed
        if name not in {item for item, _ in _section_order(text)} and body.strip():
            rebuilt.append(f"{name}:\n{body.strip()}")
    return "\n\n".join(rebuilt) + "\n"


_KNOWN_SECTIONS = (
    "SUBJECT LOCK", "CHARACTERS", "CHARACTER", "CAST", "PRESENCES", "PRESENCE",
    "CAMERA", "CINEMATOGRAPHY", "AMBIENCE", "DESCRIPTION", "STORY", "SYNOPSIS",
    "PLOT", "NEGATIVE", "REFERENCES", "STYLE", "VOICES", "AUDIO",
)


def reflow_brief(brief: str) -> str:
    """Put the sections back on their own lines when a brief arrives as one long line.

    Measured on a real project: a 7,629-character brief reached the pipeline with no
    newlines at all — ``SUBJECT LOCK: … CHARACTERS: … DESCRIPTION: Stage 1 (Valley
    Overview): … NEGATIVE: … REFERENCES: …`` — so every reader that works line by line saw
    one section, the description had no boundary, and the cast and the negative were parsed
    as part of the prose. A brief is written in sections; this restores the line breaks the
    text already implies.
    """

    text = str(brief or "")
    if not text or text.count("\n") >= 3:
        return text
    for section in _KNOWN_SECTIONS:
        text = re.sub(
            rf"(?<!\n)\s*\b({section})\s*:",
            lambda match: f"\n\n{match.group(1)}:",
            text,
        )
    return text.lstrip("\n")


def _section_order(brief: str) -> list[tuple[str, str]]:
    """The sections of a brief, in the order they are written, unparsed.

    Only a heading this module knows is a section. A brief may open its DESCRIPTION with
    its own label ("STANDING RULE: the narrator is inside every scene she describes"),
    and treating that label as a section moved the description into a section of its own
    and left STANDING RULE where the prose belonged.
    """

    known = {
        "SUBJECT LOCK", "CHARACTERS", "CHARACTER", "CAST", "PRESENCES", "PRESENCE",
        "CAMERA", "CINEMATOGRAPHY", "AMBIENCE", "DESCRIPTION", "STORY", "SYNOPSIS",
        "PLOT", "NEGATIVE", "REFERENCES", "STYLE", "VOICES", "AUDIO",
    }
    brief = reflow_brief(brief)
    order: list[tuple[str, str]] = []
    current = ""
    lines: list[str] = []
    for line in str(brief or "").splitlines():
        heading = re.match(
            r"^[ \t]*([A-Z][A-Z0-9 /_\-()]{2,50}?)\s*(?:\([^)]*\))?\s*:\s*(.*)$", line
        )
        if heading and " ".join(heading.group(1).split()).upper() in known:
            if current:
                order.append((current, "\n".join(lines).strip()))
            current = " ".join(heading.group(1).split()).upper()
            lines = [heading.group(2)] if heading.group(2).strip() else []
            continue
        if current:
            lines.append(line)
    if current:
        order.append((current, "\n".join(lines).strip()))
    return order


#: Sections whose prose is the person's film. A structuring pass may not rewrite them: it
#: exists to make the brief *parse*, not to have a better idea. Measured on a real project,
#: pressing Structure replaced the writer's description with the model's own version, and
#: the plan then rendered the model's film -- forty clips of the same scene with different
#: dialogue.
_PERSON_SECTIONS = (
    "DESCRIPTION", "STORY", "SYNOPSIS", "PLOT", "NEGATIVE", "CAMERA", "CINEMATOGRAPHY",
    "AMBIENCE", "STYLE",
)

#: Sections that are machine addresses rather than prose: the lock, the rows that bind a
#: name to a voice and a picture. Only these may be rewritten.
_STRUCTURE_SECTIONS = (
    "SUBJECT LOCK", "CHARACTERS", "CHARACTER", "CAST", "PRESENCES", "PRESENCE",
    "REFERENCES", "VOICES", "AUDIO",
)

#: Below this share of the writer's own words, a rewritten section is not the same film.
_STORY_KEPT_MIN = 0.55


def _story_words(text: str) -> set:
    return {
        word for word in re.findall(r"[a-zà-ɏ']{4,}", str(text or "").casefold())
        if word not in {
            "that", "this", "with", "from", "into", "their", "there", "which", "when",
            "then", "they", "them", "have", "will", "would", "about", "while", "through",
            "every", "each", "also", "been", "being", "some", "such", "than", "only",
        }
    }


def keeps_the_story(original: str, rewritten: str) -> bool:
    """Whether a rewrite still says the person's film rather than a different one."""

    before = _story_words(original)
    if not before:
        return True
    return len(before & _story_words(rewritten)) / len(before) >= _STORY_KEPT_MIN


def _merge_rows(person_body: str, model_body: str) -> str:
    """The person's rows, plus any row the model added that they had not written."""

    rows = _section_rows(person_body) or []
    if not rows:
        return person_body
    kept = list(rows)
    from .cast_vision import same_name

    for label, body in _section_rows(model_body) or []:
        if any(same_name(label, known) for known, _ in kept):
            continue
        kept.append((label, body))
    return "\n".join(f"{label}: {body}" for label, body in kept)


def _lock_is_about_the_same_people(person_body: str, model_body: str) -> bool:
    """Whether the model's lock names the participants the person's lock names."""

    from .subject_lock import parse

    person = {name.casefold() for name in parse(person_body).values()}
    model = {name.casefold() for name in parse(model_body).values()}
    if not person:
        return bool(model)
    return model == person


def merge_structured(person_brief: str, proposal: str) -> tuple:
    """Keep the person's film; take only the machine-readable structure from a rewrite.

    Returns ``(brief, replaced)`` where ``replaced`` names the sections the model tried to
    rewrite and was not allowed to, so the panel can say so instead of quietly handing back
    a different film.
    """

    person = dict(_section_order(person_brief))
    model = dict(_section_order(proposal))
    merged: dict = {}
    replaced: list = []

    for name, body in person.items():
        if name in _PERSON_SECTIONS:
            merged[name] = body
            if name in model and model[name].strip() and not keeps_the_story(body, model[name]):
                replaced.append(name)
        elif name == "SUBJECT LOCK":
            candidate = model.get(name, "")
            merged[name] = (
                candidate if candidate.strip() and _lock_is_about_the_same_people(body, candidate)
                else body
            )
            if candidate.strip() and merged[name] is body and candidate.strip() != body.strip():
                replaced.append(name)
        elif name.split()[0] in {"CHARACTERS", "CHARACTER", "CAST", "PRESENCES", "PRESENCE"}:
            merged[name] = _merge_rows(body, model.get(name, ""))
        else:
            merged[name] = model.get(name) or body

    for name, body in model.items():
        if name not in merged and body.strip():
            merged[name] = body

    rendered = "\n\n".join(
        f"{name}:\n{body.strip()}" if body.strip() else f"{name}:" for name, body in merged.items()
    )
    return rendered + "\n", replaced


def _acts_in_story(name: str, story: str, window: int = 48) -> bool:
    """Whether the story has ``name`` itself doing something visible.

    The clause the name opens is what counts: "King Saul's Tent is shown: inside ... King
    Saul sits" has "sits" nearby, but it belongs to King Saul, and the tent is an object
    the film presents.
    """

    from .scene_policy import EVENT_ACTION_RE

    for match in re.finditer(rf"\b{re.escape(name)}\b", story):
        tail = re.split(r"[.;:,]", story[match.end():match.end() + window], maxsplit=1)[0]
        if EVENT_ACTION_RE.search(tail):
            return True
    return False


def _name_words(name: str) -> list[str]:
    return [
        word.casefold()
        for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ'\-]+", name)
        if len(word) > 3 and word.casefold() not in {
            "la", "el", "los", "las", "the", "del", "de", "of", "and", "cinco", "five",
        }
    ]


_DESCRIPTION_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(description|story|synopsis|plot)\b[^\n:]{0,80}:\s*"
)
_DESCRIPTION_STOP_RE = re.compile(
    r"(?im)^[ \t]*(negative|ambience|camera|characters?[^\n:]*|subject lock|"
    r"presences?|voices?|audio[^\n:]*|style|dialogue[^\n:]*)\s*:"
)


def _description_prose(brief: str) -> str:
    """The DESCRIPTION prose, sliced from the raw text.

    Read from the raw text rather than from `_sections` on purpose: a brief may open its
    description with its own heading ("STANDING RULE: the narrator is inside every scene
    she describes"), and a section splitter that honours that heading leaves DESCRIPTION
    empty and sends this reader to the whole document instead.
    """

    text = str(brief or "")
    heading = _DESCRIPTION_HEADING_RE.search(reflow_brief(text))
    if not heading:
        return ""
    rest = reflow_brief(text)[heading.end():]
    stop = _DESCRIPTION_STOP_RE.search(rest)
    return rest[: stop.start()] if stop else rest


def _stage_list_block(brief: str) -> str:
    """The body of whichever section actually numbers the stages, if one does."""

    for _name, body in _section_order(brief):
        if re.search(
            r"(?i)\b(?:stage|beat|shot|scene)\s+\d+\s*(?:\([^)]{0,60}\))?\s*:", body
        ):
            return body
    return ""


def _stage_texts(brief: str) -> list[tuple[int, str]]:
    """``[(1, "kitchen, dawn: ..."), ...]`` — each stage and its own prose."""

    prose = _description_prose(brief)
    if not prose.strip():
        prose = str(brief or "")
    matches = list(re.finditer(
        r"(?i)\b(?:stage|beat|shot|scene)\s+(\d+)\s*(?:\(([^)]{0,60})\))?\s*:", prose
    ))
    if not matches:
        # A stage list can sit under another heading. Measured on a real brief, the person
        # numbered "Stage 1 … Stage 5" inside CAMERA and every reader, looking only at
        # DESCRIPTION, silently replaced their five intended shots with five sentences.
        numbered = str(_stage_list_block(brief) or "")
        prose = numbered or prose
        matches = list(re.finditer(
            r"(?i)\b(?:stage|beat|shot|scene)\s+(\d+)\s*(?:\(([^)]{0,60})\))?\s*:", prose
        ))
    if matches:
        stages: list[tuple[int, str]] = []
        for position, match in enumerate(matches):
            end = matches[position + 1].start() if position + 1 < len(matches) else len(prose)
            label = (match.group(2) or "").strip()
            body = " ".join(prose[match.end():end].split())
            stages.append((int(match.group(1)), f"{label}: {body}" if label else body))
        return stages
    # No numbered stages: each sentence is a beat, as the planner reads it.
    sentences = [
        " ".join(part.split())
        for part in re.split(r"(?<=[.!?])\s+", " ".join(prose.split()))
        if len(part.strip()) > 24
    ]
    return list(enumerate(sentences, start=1))


def diagnose_brief(
    brief: str,
    *,
    kind: str = SHORT_FILM_AUDIO,
    clip_count: int = 0,
    transcript_speakers: Optional[Iterable[str]] = None,
    has_audio: bool = False,
    references: Optional[Iterable[Any]] = None,
) -> dict[str, Any]:
    """Everything that will go wrong with this brief, in the code's own terms.

    Pure text analysis: no model, no GPU, no pipeline. The report is what the assistant
    panel shows and what the structuring pass is told to fix, so it has to name the
    problem precisely enough to be actionable — "the lock is not in the shape the
    compiler parses" rather than "add a lock".
    """

    text = str(brief or "")
    policy = scene_policy(kind)
    sections = _sections(text)
    findings: list[dict[str, str]] = []

    # The transcript names its voices the way the diarizer does ("SPEAKER_00"); everything
    # downstream speaks "(S1)". Canonicalize once, here, so every message and every prompt
    # speaks the language the compiler parses.
    from .cast_vision import canonical_speaker_labels

    raw_speakers = [
        str(speaker).strip() for speaker in (transcript_speakers or ()) if str(speaker).strip()
    ]
    canonical_speakers = canonical_speaker_labels(raw_speakers)
    speaker_map = dict(zip(raw_speakers, canonical_speakers))

    def add(code: str, severity: str, message: str, fix: str) -> None:
        findings.append({
            "code": code, "severity": severity, "message": message, "fix": fix,
        })

    if not text.strip():
        add("empty-brief", "error",
            "The project text is empty, so the plan has nothing but the audio.",
            "Describe the cast, the world and the film's beats.")
        return {"kind": policy.kind, "label": policy.label, "findings": findings,
                "sections": [], "cast": [], "stages": 0, "chars": 0,
                "speakers": canonical_speakers, "speaker_map": speaker_map}

    # 1. The lock. This one decides how every participant is numbered.
    from services.director.h3_dialogue import project_subject_lock, project_subject_lock_cues

    lock = project_subject_lock(text)
    lock_cues = project_subject_lock_cues(text)
    cast_rows = _cast_rows(text)
    if not lock:
        add("no-subject-lock", "error",
            "No SUBJECT LOCK the compiler can parse, so which participant is which "
            "<Subject N> is inferred from the planner's own list — the mistake that puts "
            "one character's voice on another.",
            "Write '<Subject 1> is ALWAYS <name>.' and '<Subject 2> is ALWAYS <name>.'")

    # 1b. The lock names the participant; the audio has a voice. Nothing tied the two
    # together, so a plan described the narrator with no (S1) and the compiler had to
    # infer the speaker from row order.
    raw_cues = _raw_lock_cues(text)
    wrong_cue = [cue for cue in raw_cues if not _is_canonical_cue(cue)]
    if lock and has_audio and wrong_cue:
        add("lock-cue-not-canonical", "error",
            "The SUBJECT LOCK labels a participant with "
            f"{', '.join(sorted(wrong_cue))}, which is the diarizer's id and not the "
            "speaker label the compiler reads. The lock then parses to a participant with "
            "no voice, and a line cannot be attributed.",
            f"Write the label the transcript uses: '{canonical_speakers[0] if canonical_speakers else 'S1'}' "
            f"as '(<label>)' — '<Subject 1> (S1) is ALWAYS <name>.'")
    if lock and has_audio and not lock_cues:
        speakers_here = canonical_speakers
        add("lock-missing-cue", "warning",
            "The SUBJECT LOCK names the participants but binds none of them to the "
            "audio's speaker label"
            + (f" ({', '.join(speakers_here)})" if speakers_here else "")
            + ", so which <Subject N> owns a spoken line is left to row order.",
            "Write the cue beside the name: '<Subject 1> (S1) is ALWAYS Anita.'")

    # 2. Stages. Short film only: a podcast is one studio by design.
    stages = _stage_texts(text)
    # A brief that describes how the film is made, in a workflow whose narration supplies
    # each clip's moment, is *meant* to have no shot list: the cast is a standing roster
    # and no stage has to name everybody. Checks that assume stages must stand down.
    narration_carries_it = bool(has_audio) and not description_states_events(text)
    if policy.max_consecutive_same_setting > 0 and not narration_carries_it:
        if not stages:
            add("no-stages", "error",
                "No stages, so a clip has no position in the story: characters appear "
                "and disappear between shots.",
                "Split DESCRIPTION into 'Stage 1 (label): …' beats, in order.")
        elif clip_count and len(stages) < 3:
            add("few-stages", "warning",
                f"{len(stages)} beat(s) for {clip_count} clips: most clips will have to "
                "invent their own moment.",
                "Write at least 5 beats, or let the assistant expand them.")
        for number, body in stages:
            places = {
                word for word in _PLACE_WORDS
                if re.search(rf"\b{word}\b", body, re.I)
            }
            if len(places) >= 3:
                add("stage-wanders", "warning",
                    f"Stage {number} names {len(places)} places ({', '.join(sorted(places)[:4])}); "
                    "one clip is one shot in one place, so this renders as a montage.",
                    "Split it into one stage per place.")

    # 3. A camera habit reaches every shot.
    camera = ""
    for name, body in sections.items():
        if name.startswith(("CAMERA", "CINEMATOGRAPHY", "LENS")):
            camera = body
            break
    lowered_camera = camera.casefold()
    for word in _GLOBAL_RULE_WORDS:
        if word in lowered_camera:
            add("camera-habit", "warning",
                f"The camera section says '{word}': the planner reads that as a rule for "
                "every shot, so the same move repeats through the film.",
                "Write the camera per stage, and keep the general look to lens and palette.")

    # 4. Cast and stages disagree.
    if policy.max_consecutive_same_setting > 0 and stages and not narration_carries_it:
        stage_text = " ".join(body for _, body in stages).casefold()
        unplaced = [
            name for name in cast_rows
            if (words := _name_words(name))
            and not any(re.search(rf"\b{re.escape(word)}\b", stage_text) for word in words)
        ]
        if unplaced:
            add("cast-without-stage", "warning",
                f"{len(unplaced)} cast entr{'y' if len(unplaced) == 1 else 'ies'} no stage "
                f"names ({', '.join(unplaced[:4])}"
                + (", ..." if len(unplaced) > 4 else "")
                + "), so the planner has to guess where they belong.",
                "Say in which stage each one appears, or move them out of CHARACTERS.")

    # 5. Characters named in the story but never described.
    if stages:
        from .cast_vision import same_name

        # A PRESENCES row is a cast member too, and only CHARACTERS rows were compared.
        # Measured on the David-y-Goliat brief, whose lock names "the Israelite Soldiers":
        # "Israelite" was then reported as a character the story invents an appearance
        # for, when the row describing those soldiers sat in the brief all along.
        listed_words = {word for name in cast_rows for word in _name_words(name)}

        story = " ".join(body for _, body in stages)
        for match in re.finditer(
            r"\b([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})?)", story
        ):
            candidate = match.group(1)
            lowered = candidate.casefold()
            if any(same_name(candidate, name) for name in cast_rows):
                continue
            if lowered.split()[0] in listed_words:
                continue
            # A place ("Kitchen:") and a sentence opener are not cast.
            if lowered.split()[0] in _PLACE_WORDS:
                continue
            # "Beat 3", "Shot 2": a planning unit followed by its number.
            if re.match(
                r"\s*(?:#|no\.?)?\s*\d",
                story[match.end():],
                re.IGNORECASE,
            ) and lowered.split()[0] in _PIPELINE_VOCAB:
                continue
            if story[match.end():match.end() + 1] == ":":
                continue
            if lowered.split()[0] in _NOT_A_NAME:
                continue
            if story[match.end():match.end() + 1] == ":":
                continue
            # A name used once is usually the town or the night; a person the story
            # acts with recurs. A two-word name is specific enough to flag either way.
            appearances = len(re.findall(rf"(?i)\b{re.escape(candidate)}\b", story))
            if len(candidate.split()) == 1:
                if appearances < 2:
                    continue
                # A single word must also be doing something: "King Saul's Tent is
                # shown:" made "Tent" an undescribed character on a real brief, and a
                # warning about a person who does not exist costs more trust than one
                # that is missed.
                if not _acts_in_story(candidate, story):
                    continue
            add("undescribed-character", "warning",
                f"'{candidate}' appears in the story but has no entry in CHARACTERS, so the "
                "model invents an appearance for it.",
                f"Add '{candidate}: …' to CHARACTERS, or remove it from the story.")
            break

    # 6. The audio speaks with someone the brief does not cast.
    speakers = {
        str(speaker).strip()
        for speaker in (transcript_speakers or ())
        if str(speaker).strip()
    }
    if has_audio and speakers and not lock:
        add("speakers-unbound", "warning",
            f"The audio has {len(speakers)} speaker(s) and the brief binds none of them to "
            "a Subject.",
            "Number each speaker in the lock: '<Subject 1> is ALWAYS <name>.'")

    # 6a. A supplied picture with no appearance in the text. Measured on a real project
    # three pictures were attached (Anita, David, Goliath) and the brief gave Anita one
    # adjective and the other two nothing at all, so each shot invented them.
    if references:
        from .cast_vision import reference_roster, same_name

        roster = reference_roster(references)
        unbound: list[str] = []
        for item in roster:
            match = next(
                (body for name, body in cast_rows.items() if same_name(name, item["name"])),
                "",
            )
            if not _describes_appearance(match):
                unbound.append(item["name"])
        if unbound:
            add("cast-not-bound-to-picture", "warning",
                f"{len(unbound)} supplied picture(s) name a character the brief gives no "
                f"appearance ({', '.join(unbound[:4])}"
                + (", ..." if len(unbound) > 4 else "")
                + "), so nothing in the text holds the face and each shot invents one.",
                "Press Structure: it reads the appearance from each picture. If the rows "
                "still have no hair or wardrobe, the local model has no vision (load one "
                "with mmproj), or write hair, build and wardrobe yourself.")

    if not references:
        add("no-references", "info",
            "No character reference picture reached this check, so the appearance can "
            "only come from the text.",
            "Add the character pictures to the project, then press Structure — the "
            "assistant reads them instead of asking you to describe a face.")
    # real brief: "<every other character>: named as presences and objects" and the
    # heading "PRESENCES (only what this workflow needs):" were both copied verbatim.
    placeholders = [
        line.strip() for line in text.splitlines() if _PLACEHOLDER_LINE_RE.match(line)
    ]
    if placeholders:
        add("template-placeholder", "error",
            f"{len(placeholders)} line(s) are template placeholders, not content "
            f"({', '.join(placeholders[:2])[:80]})",
            "Remove them, or press Structure again — the assistant strips them now.")

    # 6c. A stage that only describes the format leaves the planner to invent the film —
    # unless a driving narration supplies each clip's moment, in which case the brief is
    # *meant* to be a contract and the stages are not used at all.
    if policy.max_consecutive_same_setting > 0 and stages:
        abstract = [
            number for number, body in stages
            if not _CONCRETE_ACTION_RE.search(body)
            and not {word for word in _PLACE_WORDS if re.search(rf"\b{word}\b", body, re.I)}
        ]
        narration_carries_it = bool(has_audio) and not description_states_events(text)
        if narration_carries_it:
            add("narration-driven", "info",
                "DESCRIPTION describes how the film is made rather than what happens in "
                "it. With a driving narration that is correct: every clip takes its "
                "moment from the audio and no stage is imposed on it.",
                "Nothing to change. Keep the narrator's position in the action explicit, "
                "as a standing rule, so every clip shows it.")
        elif abstract:
            numbers = ", ".join(str(number) for number in abstract[:4])
            add("abstract-beats", "warning",
                f"{len(abstract)} stage(s) name no place and no action ({numbers}"
                + (", ..." if len(abstract) > 4 else "")
                + "): a number is a promise of a shot, and the planner has to invent "
                "what is inside it.",
                "Replace the number with the place and the action it stands for: "
                "'Stage 3: the cobbled street at dusk — the children lift their lanterns "
                "and walk past the well', not 'Stage 3'.")

    # 6d. Someone the story acts with, listed only as a presence. Measured on the
    # David-y-Goliat brief: "David: presence in the biblical action stages." — David and
    # Goliat act in four of five stages and no appearance is ever given, so each clip
    # drew its own David. That is the face that changes as the run goes on.
    character_section = next(
        (body for name, body in sections.items() if name.startswith("CHARACTER")), ""
    )
    described_here = {
        " ".join(line.split(":", 1)[0].split()).casefold()
        for line in character_section.splitlines()
        if ":" in line and re.match(r"^\s*[-*]?\s*[A-ZÁÉÍÓÚÜÑ]", line)
    }
    stage_blob = " ".join(body for _number, body in stages)
    presence_lines = [
        line for line in sections.get("PRESENCES", "").splitlines()
        if re.match(r"^\s*[-*]?\s*[A-ZÁÉÍÓÚÜÑ][^:\n]{2,40}:\s*\S", line)
    ]
    acting_rows = [
        " ".join(line.split(":", 1)[0].split())
        for line in presence_lines
        if not _describes_appearance(line.split(":", 1)[1])
    ]
    acting_rows = [
        name for name in acting_rows
        if name.casefold() not in described_here
        and any(
            re.search(rf"\b{re.escape(word)}\b", stage_blob, re.I)
            for word in (_name_words(name) or [name.casefold()])
        )
    ]
    if acting_rows and not narration_carries_it:
        add("cast-without-appearance", "warning",
            f"{len(acting_rows)} character(s) act in the story but are only listed as "
            f"presences ({', '.join(acting_rows[:3])}"
            + (", ..." if len(acting_rows) > 3 else "")
            + "): they have no appearance, so every clip invents a different one.",
            "Give each one a CHARACTERS row with age, build, hair, wardrobe.")

    if "NEGATIVE" not in sections:
        add("no-negative", "info",
            "No NEGATIVE section: what must not appear is left to the model.",
            "Add 'NEGATIVE:' with the exclusions that matter (extra characters, gore, "
            "generated speech).")

    # 7. What this workflow expects at all.
    if policy.kind == PODCAST and stages:
        add("podcast-stages", "info",
            f"This is a {policy.label}: it is one studio and one conversation, so staged "
            "locations are not needed — coverage is.",
            "Keep one setting and describe the coverage instead.")
    if policy.kind == MUSIC_VIDEO and not re.search(r"(?i)chorus|verse|bridge|section", text):
        add("music-no-sections", "info",
            "A music video is planned against the song's sections, and none are named.",
            "Name what happens in the verse, the chorus and the bridge.")

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda item: order.get(item["severity"], 3))
    return {
        "kind": policy.kind,
        "label": policy.label,
        "findings": findings,
        "sections": list(sections.keys()),
        "cast": list(cast_rows.keys()),
        "stages": len(stages),
        "chars": len(text),
        "subject_lock": {int(number): name for number, name in lock.items()},
        "lock_cues": lock_cues,
        "speakers": canonical_speakers,
        "speaker_map": speaker_map,
    }


def normalized_brief(
    brief: str,
    *,
    kind: str = SHORT_FILM_AUDIO,
    transcript_speakers: Optional[Iterable[Any]] = None,
    references: Optional[Iterable[Any]] = None,
) -> str:
    """The same brief with the mechanical faults repaired, no model involved.

    This is what the assistant can guarantee without an LLM: headings in the canonical
    order and case, `Stage N` numbering restored, the SUBJECT LOCK derived when the answer
    is unambiguous, cast rows the story never places given a placeholder stage note
    removed, and the whole thing trimmed. Ideas are untouched.
    """

    policy = scene_policy(kind)
    sections = _sections(strip_template_placeholders(brief))
    ordered: list[str] = []
    for wanted in CANONICAL_SECTIONS:
        for name, body in sections.items():
            if name.startswith(wanted.split()[0]) and body.strip():
                ordered.append(f"{wanted}:\n{body.strip()}")
                break
    # Anything the canonical list does not name is kept, at the end: a director's own
    # section is not the assistant's to delete.
    for name, body in sections.items():
        if any(name.startswith(wanted.split()[0]) for wanted in CANONICAL_SECTIONS):
            continue
        if body.strip():
            ordered.append(f"{name}:\n{body.strip()}")
    if not ordered:
        ordered = ["DESCRIPTION:\n" + " ".join(str(brief or "").split())]

    text = "\n\n".join(ordered)
    text = normalize_speaker_cues(text, transcript_speakers)
    text = consolidate_cast_rows(text)
    text = derive_subject_lock(text, transcript_speakers, references)

    # Numbering the beats of a brief that describes the film instead of its events
    # would invent a shot list out of a contract: a narrated documentary would be
    # pinned to one constructed location per stage. Leave it as prose; the narration
    # supplies each clip's moment.
    if policy.max_consecutive_same_setting > 0 and description_states_events(text):
        text = _number_stages(text)
    return text.strip()


def _number_stages(text: str) -> str:
    """Give the DESCRIPTION's beats ``Stage N:`` headings, but only the ones that are beats.

    A number is a promise: the planner reads ``Stage 4:`` as "here is a shot", and a
    sentence that names no place and no action leaves it to invent both. Measured on a real
    brief, seventeen sentences of prose about *how* the film is made were frozen into
    ``Stage 1`` … ``Stage 17``, and the check then reported all seventeen as stages that
    name nothing — which is true, and was caused here.

    So a sentence becomes a stage only when it says where and what happens. A sentence that
    does not is context: it stays in the description, before the numbered stages, where it
    reads as a rule rather than a shot.
    """

    heading = re.search(r"(?im)^[ \t]*(DESCRIPTION|STORY|NARRATIVE)\b[^\n:]{0,60}:\s*", text)
    if not heading:
        return text
    rest_start = heading.end()
    following = re.search(
        r"(?im)^[ \t]*(NEGATIVE|AMBIENCE|CAMERA|CHARACTERS?|PRESENCES|VOICES?|"
        r"AUDIO[^\n:]*|STYLE|MEANING)\s*:",
        text[rest_start:],
    )
    body_end = rest_start + (following.start() if following else len(text) - rest_start)
    body = text[rest_start:body_end]
    if re.search(r"(?i)(?:stage|beat|shot|scene)\s+\d", body):
        return text
    sentences = [
        " ".join(part.split())
        for part in re.split(r"(?<=[.!?])\s+", " ".join(body.split()))
        if part.strip()
    ]
    # A fragment with no beat in it ("One night.") opens the beat that follows it.
    merged: list[str] = []
    carry = ""
    for sentence in sentences:
        if carry:
            sentence = f"{carry} {sentence}"
            carry = ""
        if len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ']+", sentence)) < 6 and ":" not in sentence:
            carry = sentence
            continue
        merged.append(sentence)
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    if not merged:
        return text
    # A number is only given to a sentence that can be shot.
    context: list[str] = []
    beats: list[str] = []
    for sentence in merged:
        if _is_shootable(sentence):
            beats.append(sentence)
        elif beats:
            # It qualifies the stage just named ("the fog does not lift"), so it belongs
            # to that stage rather than to a numbered one of its own.
            beats[-1] = f"{beats[-1]} {sentence}"
        else:
            context.append(sentence)
    if not beats:
        return text
    staged = "\n".join(
        f"Stage {index}: {sentence}"
        for index, sentence in enumerate(beats, start=1)
    )
    if context:
        staged = "\n".join(context) + "\n" + staged
    tail = text[body_end:]
    if tail and not tail.startswith("\n\n"):
        tail = "\n" + tail.lstrip("\n")
    return text[:rest_start] + "\n" + staged.strip("\n") + "\n" + tail


def _is_shootable(sentence: str) -> bool:
    """Whether a sentence can be a shot: a place, or somebody visibly doing something.

    A habit is not a shot. "Anita always stands at the centre of the cinematic action"
    contains a verb and no place, and numbering it made the planner read a rule as a
    scene — the seventeen abstract stages measured on a real brief.
    """

    body = str(sentence or "")
    if re.search(r"\b(always|every|never|whenever|throughout|constantly|all the time)\b", body, re.I):
        return False
    if any(re.search(rf"\b{word}\b", body, re.I) for word in _PLACE_WORDS):
        return True
    return beat_states_an_event(body)


def structure_prompt(
    brief: str,
    *,
    kind: str = SHORT_FILM_AUDIO,
    diagnosis: Optional[dict[str, Any]] = None,
    clip_count: int = 0,
    transcript_speakers: Optional[Iterable[str]] = None,
    characters: Optional[Iterable[str]] = None,
    has_audio: Optional[bool] = None,
    roster: Optional[list[dict]] = None,
) -> tuple[str, str]:
    """``(system, user)`` for the pass that rewrites the brief into the canonical shape.

    The model's job is only ever to reorganise and fill gaps: the person's ideas, names,
    images and beats are what the film is. It may not invent a character or a scene that
    is not in the text, and it must keep every name the person wrote.

    Two shapes, decided by the text itself. A brief that tells the story gets numbered
    stages. A brief that describes *how the film is made* — a narrated documentary where
    the narrator walks through the scenes she describes — must not be given stages at
    all: the narration supplies each clip's moment, and stages invented from a contract
    pin the narrator to a location the film never asked for.
    """

    policy = scene_policy(kind)
    narration_driven = (
        description_states_events(brief) is False
        and (bool(transcript_speakers) if has_audio is None else bool(has_audio))
    )
    report = diagnosis or diagnose_brief(
        brief, kind=kind, clip_count=clip_count,
        transcript_speakers=transcript_speakers,
        has_audio=bool(transcript_speakers) if has_audio is None else bool(has_audio),
    )
    problems = "\n".join(
        f"- [{finding['severity']}] ({finding['code']}) {finding['message']} "
        f"→ {finding['fix']}"
        for finding in report.get("findings", [])
    ) or "- none"
    speakers = ", ".join(sorted(str(s) for s in (transcript_speakers or ()))) or "(none supplied)"
    cast = ", ".join(sorted(str(c) for c in (characters or ()))) or "(not supplied)"
    from .cast_vision import canonical_speaker_labels, roster_block

    speaker_labels = canonical_speaker_labels(transcript_speakers or ())
    pictures = roster_block(roster or [])

    if narration_driven:
        camera_rule = (
            '- CAMERA: the lens, the palette, and where the narrator stands relative to the '
            "action. No per-stage list: there are no stages. Never \"whenever\", \"always\", "
            "\"every time\" or \"throughout\"."
        )
        description_rule = (
            "- DESCRIPTION: the story is told by the narration, not by this section. Do NOT "
            "write Stage 1, Stage 2 or any numbered stage, and do not invent a location the "
            "narrator visits. Write two things instead: (a) one STANDING line saying where "
            "the narrator is relative to the action in every shot, in the person's own "
            "terms — inside the scene, moving through the settings, at the centre of what "
            "is happening, the figures reacting around her; and (b) the world the narration "
            "moves through, as the person described it. Example shape: \"STANDING RULE: the "
            "narrator is inside every scene she describes, never beside it.\""
        )
        extra_rules = (
            "- The narration decides each shot's moment. Your job is to state who is always "
            "on screen, how they look, the world, and the rules — not to list events.\n"
            "- If the person says the narrator moves through the story, is at the centre of "
            "the action, or that the figures appear around her, that is a standing rule for "
            "every shot and it must survive the rewrite word for word in meaning. Never "
            "relocate the narrator to a studio, a desk, a ridge or any other place the "
            "person did not write.\n"
        )
    else:
        camera_rule = (
            '- CAMERA: the lens and palette, then one line per stage. Never "whenever", '
            '"always", "every time" or "throughout".'
        )
        description_rule = (
            "- DESCRIPTION: one stage per story beat, written \"Stage N (label): ...\" and "
            "each stage names ONE place, WHO is in it, and WHAT visibly happens there."
        )
        extra_rules = ""

    system = f"""You reorganise a film brief into the shape a shot planner can read. You are
not writing a new film. Every name, image, action and idea in the user's text is theirs:
keep them, in their order, and only reorganise and fill the gaps named below.

Output ONLY the rewritten brief. Use exactly these section headings, in this order:
SUBJECT LOCK, CHARACTERS, PRESENCES, CAMERA, AMBIENCE, DESCRIPTION, NEGATIVE.

What each section holds:
- SUBJECT LOCK: one line per speaking participant, written exactly as
  "<Subject 1> (S1) is ALWAYS <name>."  and  "<Subject 2> (S2) is ALWAYS <name>."
  The cue in parentheses is the audio's speaker label and it is NOT optional: it is the
  only thing that says which numbered participant owns which voice. Keep every cue the
  text already carries, and add one from the roster below when a picture states it.
  The cue must be written exactly as one of these: {", ".join(speaker_labels) or "(S1)"}.
  Never write a diarization id such as SPEAKER_00 — that form does not parse, and the
  whole lock is then discarded.
  Then one sentence naming the characters that never become a Subject.
- CHARACTERS: one line per character, "<name>: appearance, age, wardrobe."
  Every character the story shows by name gets its own line. Never replace named
  people with a group. If the text never describes someone's face or clothes, write
  only what the text implies they are (era, role, age if stated) and stop there: no
  invented hair colour, no invented costume, and never the word "presence". A line
  that describes when someone appears instead of how they look is a fault, because
  every shot then invents a different face. Keep the person's own adjectives for a
  character they did describe ("young, beautiful and charismatic" stays theirs).
- PRESENCES: only the characters that appear without speaking, one line each. A named
  group the person listed ("the Israelite soldiers") belongs here, with what it looks
  like.
{camera_rule}
- AMBIENCE: place, palette, texture, light.
{description_rule}
- NEGATIVE: what must not appear.
- REFERENCES: copy the roster you are given, unchanged. Every name in it already has a
  supplied picture, so write that name exactly as the roster writes it (with its cue when
  it has one), keep its <Picture N> number exactly as given, and never add, remove or
  renumber an entry.

Hard rules:
- This brief is for a {policy.label}. {policy.guidance}
- Every character with a picture already looks like something. Copy the appearance you
  are given for it and never invent a different one: a mismatch between the text and the
  picture is re-drawn in every shot. Never write a <Picture N> tag inside CHARACTERS or
  DESCRIPTION: the number belongs to the renderer, and the roster is where it is stated.
- A stage is ONE place and ONE moment. If a beat names several places, split it.
- A character exists only in the stage where the story puts it. Never move anyone into
  a stage to make it busier.
- Every stage must name a place and a visible action. Never write a stage that only
  describes the format ("epic scenes illustrate what is being narrated"), because the
  planner then has to invent the film from it.
{extra_rules}- Never output a line whose key is in angle brackets, and never repeat an instruction
  from this prompt as if it were content.
- Keep the person's own words for names, places and images; you may translate
  structure, not content.
- Never invent a character, an event or a location that is not in the text. If
  something needed is missing, state the gap in NEGATIVE as a prohibition.
- Total length must stay close to the original: this text is read by a planner, not by
  a model with unlimited context."""

    user = f"""The film is planned as {clip_count or 'an unknown number of'} clips.
The audio's speakers, in first-appearance order, are: {", ".join(speaker_labels) or "(none supplied)"}
(their raw transcript ids are {speakers}; never copy those into the brief)
The cast supplied with the project is: {cast}

REFERENCE PICTURES THE MODEL WILL BE GIVEN:
{pictures or '(none supplied)'}

PROBLEMS TO FIX (from a mechanical check of the same text):
{problems}

THE BRIEF AS THE PERSON WROTE IT:
---
{str(brief or '').strip() or '(empty)'}
---

Rewrite it in the sections above. Keep every idea."""
    return system, user
