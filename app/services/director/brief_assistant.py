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
    sections = _sections(brief)
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

Output ONLY the rewritten brief, in exactly these sections, in this order, with these
headings:

SUBJECT LOCK:
<Subject 1> is ALWAYS <name>.
<Subject 2> is ALWAYS <name>.
<every other character>: named as presences and objects that never become S1 or S2.

CHARACTERS:
<name>: appearance, wardrobe, age, one line each.

PRESENCES (only what this workflow needs):
<name>: appearance, and the stage it appears in.

CAMERA:
<one line per stage or section; never "whenever", "always", "every time", "throughout">.

AMBIENCE:
<place, palette, texture, light — the world, not the plot.>

DESCRIPTION:
Stage 1 (<label>): <one place, one moment, who is in frame.>
Stage 2 (<label>): …

NEGATIVE:
<what must not appear.>

Rules:
- This brief is for a {policy.label}. {policy.guidance}
- One stage is ONE place and ONE moment. If a stage names several places, split it.
- A character exists only in the stage where the story puts it. Do not move anyone into
  a stage to make it busier.
- Keep the person's language where it is a name or an image; you may translate structure,
  not content.
- Never invent a character, an event or a location that is not in the text. If something
  needed is missing, state the gap in NEGATIVE as a prohibition instead of inventing it.
- Total length must stay close to the original: this text is read by a planner, not by a
  model with unlimited context."""

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
