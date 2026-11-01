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
    MUSIC_VIDEO,
    PODCAST,
    SHORT_FILM_AUDIO,
    SHORT_FILM_STORY,
    VIRAL_VIDEO,
    scene_policy,
)

__all__ = [
    "CANONICAL_SECTIONS",
    "diagnose_brief",
    "normalized_brief",
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
_PLACE_WORDS = (
    "kitchen", "corridor", "hallway", "church", "chapel", "altar", "sacristy", "street",
    "square", "courtyard", "doorway", "stairs", "tower", "forest", "clearing", "field",
    "bedroom", "cellar", "attic", "office", "stage", "rooftop", "balcony", "bridge",
    "river", "riverbank", "shore", "beach", "cave", "room", "house", "village", "town",
)

_CAST_ROW_RE = re.compile(
    r"(?im)^[ \t]*(?:S(\d)\s*)?[\(\[]?([A-ZÁÉÍÓÚÜÑ][^:\n]{2,60}?)[\)\]]?\s*:\s*\S"
)
_STAGE_RE = re.compile(
    r"(?i)\bstage\s+(\d+)\b|^\s*(?:scene|beat|section)\s+(\d+)\b", re.MULTILINE,
)

# A line whose key is in angle brackets is a template placeholder, not content.
# Measured on a real brief the assistant produced: the writer copied
# "<every other character>: named as presences and objects" straight out of the
# instruction, and the planner then read a cast row with no character in it. The
# heading "PRESENCES (only what this workflow needs):" leaked the same way.
_PLACEHOLDER_LINE_RE = re.compile(r"^\s*<[^>\n]{1,60}>\s*:")
_INSTRUCTION_HEADING_RE = re.compile(r"\s*\((?:only )?what (?:this workflow|the workflow)[^)]*\)")

# A stage is concrete when somebody does something in it. A beat that only describes
# the format -- "epic scenes illustrate what is being narrated" -- leaves the planner
# to invent the whole film from it, which is the wandering the user reported.
_CONCRETE_ACTION_RE = re.compile(
    r"\b(walks?|walking|enters?|entering|kneels?|kneeling|stands?|standing|sits?|sitting|"
    r"turns?|turning|raises?|raising|lifts?|holds?|holding|carries?|carrying|throws?|"
    r"throwing|strikes?|striking|climbs?|climbing|runs?|running|opens?|closes?|"
    r"falls?|falling|rises?|arrives?|leaves?|steps?|crosses?|reaches?|touches?|"
    r"prays?|praying|fights?|fighting|draws?|aims?|shoots?|washes?|builds?|"
    r"sleeps?|wakes?|weeps?|cries?|laughs?|shouts?)\b",
    re.I,
)


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
    r"rosary|earrings?|spectacles|glasses)\b|"
    r"\b(?:about|around|aged?)\s+\d{1,3}\b|\b\d{1,3}\s*(?:years? old|-year-old)\b",
    re.I,
)


def _describes_appearance(text: str) -> bool:
    """True when a cast row says what someone looks like, not merely when they appear."""

    return bool(_APPEARANCE_RE.search(text))


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
        cleaned = _INSTRUCTION_HEADING_RE.sub("", line)
        if cleaned.strip() or not line.strip():
            kept.append(cleaned)
    return "\n".join(kept)


def _sections(brief: str) -> dict[str, str]:
    """``{"SUBJECT LOCK": "...", ...}`` for the headed sections of a brief."""

    found: dict[str, str] = {}
    current = ""
    lines: list[str] = []
    for line in str(brief or "").splitlines():
        heading = re.match(r"^[ \t]*([A-Z][A-Z0-9 /_\-()]{2,50}?)\s*(?:\([^)]*\))?\s*:\s*(.*)$", line)
        if heading:
            if current:
                found[current] = "\n".join(lines).strip()
            current = " ".join(heading.group(1).split()).upper()
            lines = [heading.group(2)] if heading.group(2).strip() else []
            continue
        if current:
            lines.append(line)
    if current:
        found[current] = "\n".join(lines).strip()
    return found


def _cast_rows(brief: str) -> dict[str, str]:
    """``{"Abuela Rosa": "<its description>", ...}`` from CHARACTERS / PRESENCES."""

    rows: dict[str, str] = {}
    sections = _sections(brief)
    for name in ("CHARACTERS", "PRESENCES", "CAST", "CHARACTER PROFILES"):
        for section_name, text in sections.items():
            if not section_name.startswith(name.split()[0]):
                continue
            for line in text.splitlines():
                match = re.match(r"^\s*(?:[-*]\s*)?S?\d?\s*[\)\].]?\s*([^:\n]{2,50}?)\s*:\s*(.+)$", line)
                if not match:
                    continue
                label = " ".join(match.group(1).split())
                rows.setdefault(label, " ".join(match.group(2).split()))
    return rows


def _name_words(name: str) -> list[str]:
    return [
        word.casefold()
        for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ'\-]+", name)
        if len(word) > 3 and word.casefold() not in {
            "la", "el", "los", "las", "the", "del", "de", "of", "and", "cinco", "five",
        }
    ]


def _stage_texts(brief: str) -> list[tuple[int, str]]:
    """``[(1, "kitchen, dawn: ..."), ...]`` — each stage and its own prose."""

    sections = _sections(brief)
    prose = ""
    for name, text in sections.items():
        if name.startswith(("DESCRIPTION", "STORY", "NARRATIVE", "PLOT", "SYNOPSIS")):
            prose = text
            break
    if not prose:
        prose = str(brief or "")
    matches = list(re.finditer(r"(?i)stage\s+(\d+)\s*(?:\(([^)]{0,60})\))?\s*:", prose))
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

    def add(code: str, severity: str, message: str, fix: str) -> None:
        findings.append({
            "code": code, "severity": severity, "message": message, "fix": fix,
        })

    if not text.strip():
        add("empty-brief", "error",
            "The project text is empty, so the plan has nothing but the audio.",
            "Describe the cast, the world and the film's beats.")
        return {"kind": policy.kind, "label": policy.label, "findings": findings,
                "sections": [], "cast": [], "stages": 0, "chars": 0}

    # 1. The lock. This one decides how every participant is numbered.
    from services.director.h3_dialogue import project_subject_lock

    lock = project_subject_lock(text)
    cast_rows = _cast_rows(text)
    if not lock:
        add("no-subject-lock", "error",
            "No SUBJECT LOCK the compiler can parse, so which participant is which "
            "<Subject N> is inferred from the planner's own list — the mistake that puts "
            "one character's voice on another.",
            "Write '<Subject 1> is ALWAYS <name>.' and '<Subject 2> is ALWAYS <name>.'")

    # 2. Stages. Short film only: a podcast is one studio by design.
    stages = _stage_texts(text)
    if policy.max_consecutive_same_setting > 0:
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
    if policy.max_consecutive_same_setting > 0 and stages:
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
    described = {name.casefold() for name in cast_rows}
    if stages:
        story = " ".join(body for _, body in stages)
        for match in re.finditer(
            r"\b([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})?)", story
        ):
            candidate = match.group(1)
            lowered = candidate.casefold()
            if any(lowered in name for name in described):
                continue
            # A place ("Kitchen:") and a sentence opener are not cast.
            if lowered.split()[0] in _PLACE_WORDS:
                continue
            if story[match.end():match.end() + 1] == ":":
                continue
            if lowered.split()[0] in {
                "stage", "no", "the", "and", "only", "small", "narrow", "one", "then",
            }:
                continue
            # A name used once is usually the town or the night; a person the story
            # acts with recurs. A two-word name is specific enough to flag either way.
            appearances = len(re.findall(rf"(?i)\b{re.escape(candidate)}\b", story))
            if appearances < 2 and len(candidate.split()) == 1:
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

    # 6b. The assistant's own template reaching the planner as content. Measured on a
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

    # 6c. A stage that only describes the format leaves the planner to invent the film.
    if policy.max_consecutive_same_setting > 0 and stages:
        abstract = [
            number for number, body in stages
            if not _CONCRETE_ACTION_RE.search(body)
            and not {word for word in _PLACE_WORDS if re.search(rf"\b{word}\b", body, re.I)}
        ]
        if abstract:
            add("abstract-beats", "warning",
                f"{len(abstract)} stage(s) name no place and no action "
                f"({', '.join(str(number) for number in abstract[:4])}), so the planner "
                "has to invent what happens in them.",
                "Write each stage as a place plus who is in it plus what visibly happens.")

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
    if acting_rows:
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
    }


def normalized_brief(brief: str, *, kind: str = SHORT_FILM_AUDIO) -> str:
    """The same brief with the mechanical faults repaired, no model involved.

    This is what the assistant can guarantee without an LLM: headings in the canonical
    order and case, `Stage N` numbering restored, cast rows the story never places given
    a placeholder stage note removed, and the whole thing trimmed. Ideas are untouched.
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

    if policy.max_consecutive_same_setting > 0:
        text = _number_stages(text)
    return text.strip()


def _number_stages(text: str) -> str:
    """Give the DESCRIPTION's beats ``Stage N (label):`` headings if they lack them."""

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
    if re.search(r"(?i)stage\s+\d", body):
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
    staged = "\n".join(
        f"Stage {index} (beat {index}): {sentence}"
        for index, sentence in enumerate(merged, start=1)
    )
    tail = text[body_end:]
    if tail and not tail.startswith("\n\n"):
        tail = "\n" + tail.lstrip("\n")
    return text[:rest_start] + "\n" + staged + "\n" + tail


def structure_prompt(
    brief: str,
    *,
    kind: str = SHORT_FILM_AUDIO,
    diagnosis: Optional[dict[str, Any]] = None,
    clip_count: int = 0,
    transcript_speakers: Optional[Iterable[str]] = None,
    characters: Optional[Iterable[str]] = None,
) -> tuple[str, str]:
    """``(system, user)`` for the pass that rewrites the brief into the canonical shape.

    The model's job is only ever to reorganise and fill gaps: the person's ideas, names,
    images and beats are what the film is. It may not invent a character or a scene that
    is not in the text, and it must keep every name the person wrote.
    """

    policy = scene_policy(kind)
    report = diagnosis or diagnose_brief(
        brief, kind=kind, clip_count=clip_count,
        transcript_speakers=transcript_speakers, has_audio=bool(transcript_speakers),
    )
    problems = "\n".join(
        f"- [{finding['severity']}] ({finding['code']}) {finding['message']} "
        f"→ {finding['fix']}"
        for finding in report.get("findings", [])
    ) or "- none"
    speakers = ", ".join(sorted(str(s) for s in (transcript_speakers or ()))) or "(none supplied)"
    cast = ", ".join(sorted(str(c) for c in (characters or ()))) or "(not supplied)"

    system = f"""You reorganise a film brief into the shape a shot planner can read. You are
not writing a new film. Every name, image, action and idea in the user's text is theirs:
keep them, in their order, and only reorganise and fill the gaps named below.

Output ONLY the rewritten brief. Use exactly these section headings, in this order:
SUBJECT LOCK, CHARACTERS, PRESENCES, CAMERA, AMBIENCE, DESCRIPTION, NEGATIVE.

What each section holds:
- SUBJECT LOCK: one line per speaking participant, written exactly as
  "<Subject 1> is ALWAYS <name>."  and  "<Subject 2> is ALWAYS <name>."
  Then one sentence naming the characters that never become a Subject.
- CHARACTERS: one line per character, "<name>: appearance, age, wardrobe."
  Every character the story shows by name gets its own line. Never replace named
  people with a group. If the text never describes someone's face or clothes, write
  only what the text implies they are (era, role, age if stated) and stop there: no
  invented hair colour, no invented costume, and never the word "presence". A line
  that describes when someone appears instead of how they look is a fault, because
  every shot then invents a different face.
- PRESENCES: only the characters that appear without speaking, one line each.
- CAMERA: the lens and palette, then one line per stage. Never "whenever", "always",
  "every time" or "throughout".
- AMBIENCE: place, palette, texture, light.
- DESCRIPTION: one stage per story beat, written "Stage N (label): ..." and each stage
  names ONE place, WHO is in it, and WHAT visibly happens there.
- NEGATIVE: what must not appear.

Hard rules:
- This brief is for a {policy.label}. {policy.guidance}
- A stage is ONE place and ONE moment. If a beat names several places, split it.
- A character exists only in the stage where the story puts it. Never move anyone into
  a stage to make it busier.
- Every stage must name a place and a visible action. Never write a stage that only
  describes the format ("epic scenes illustrate what is being narrated"), because the
  planner then has to invent the film from it.
- Never output a line whose key is in angle brackets, and never repeat an instruction
  from this prompt as if it were content.
- Keep the person's own words for names, places and images; you may translate
  structure, not content.
- Never invent a character, an event or a location that is not in the text. If
  something needed is missing, state the gap in NEGATIVE as a prohibition.
- Total length must stay close to the original: this text is read by a planner, not by
  a model with unlimited context."""

    user = f"""The film is planned as {clip_count or 'an unknown number of'} clips.
The audio's speakers are: {speakers}
The cast supplied with the project is: {cast}

PROBLEMS TO FIX (from a mechanical check of the same text):
{problems}

THE BRIEF AS THE PERSON WROTE IT:
---
{str(brief or '').strip() or '(empty)'}
---

Rewrite it in the sections above. Keep every idea."""
    return system, user
