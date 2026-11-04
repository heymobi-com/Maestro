"""Read who is in a shot from the reference picture, not from the brief.

A brief is written by a person and a reference picture is a photograph, and only one of
them contains a face. Measured on a real project the brief said:

    David: presence in the biblical action stages.

— a row that says *when* he appears and never *how he looks*. The picture was attached to
every clip, but nothing in the text held the face, so the planner re-invented a David
each shot and the likeness drifted. The same brief described the narrator as "una mujer
joven, bella y carismatica" while her reference showed a woman with her hair tied back, a
white shirt, a blue skirt and low heels. Two descriptions of the same person, and neither
of them the picture.

So the appearance is not asked of the writer at all. It is read from the identity
reference with the vision model, once per picture, and that sentence is what every shot
carries. It costs one local call per character per run, it cannot be invented, and it
cannot disagree with the image the model is asked to keep.

Everything here fails soft: no vision model, no image, a model that answers prose, a
raise — all return nothing and the pipeline behaves exactly as it did before.
"""

from __future__ import annotations

import inspect
import os
import re
from typing import Any, Callable, Iterable, Optional

__all__ = [
    "CAST_LOOK_PROMPT",
    "CAST_WARDROBE_FOLLOWUP",
    "LOOK_IMAGE_MAX_SIZE",
    "LOOK_LIMIT",
    "appearance_from_reference",
    "apply_cast_descriptions",
    "apply_reference_roster",
    "canonical_speaker_label",
    "canonical_speaker_labels",
    "cast_descriptions",
    "gender_of",
    "looks_block",
    "picture_map",
    "reference_roster",
    "references_for_look",
    "roster_block",
    "speaker_cue_for",
]

#: Asked of the vision model, per identity picture. A reference is often a 2K render
#: that took a long generation, and the detail in it is the reason the face stays
#: coherent, so this asks for a full character sheet rather than a caption. It stays
#: narrow about *what* to look at — the observable person — and wide about *how much*:
#: hair, brows, eyes, nose, mouth, skin, build, every garment with colour and material,
#: footwear, jewellery, marks. The picture is a character sheet; the answer must read
#: like one. Nothing about background, pose, lighting, mood or camera, because those
#: belong to the shot and would otherwise be frozen into every clip.
CAST_LOOK_PROMPT = (
    "Describe only the person in this picture, as a compact costume and continuity note for "
    "a film crew. Answer in ONE paragraph of at most 120 words, in this exact order, "
    "separated by semicolons:\n"
    "1. GENDER AND AGE — first, always: apparent gender presentation (woman, man, or "
    "androgynous) and apparent age range; then build;"
    "2. skin tone;\n"
    "3. hair: exact colour, length, how it is worn (loose, tied back, bun, braid, updo, "
    "under a headscarf), parting;\n"
    "4. face: eyebrow colour and shape, eye colour, nose, mouth, any marks or facial hair;\n"
    "5. WARDROBE — every garment from top to bottom: exact colour, fabric, fit, sleeves, "
    "length, neckline, and anything on it;\n"
    "6. footwear: type, colour, heel height;\n"
    "7. jewellery or accessories, with material and colour.\n"
    "WARDROBE IS MANDATORY: if you are running short of space, cut detail from the face, "
    "never the clothing, and never finish before naming what the person wears. Name colours "
    "precisely (bottle green, not green). Do not describe the background, pose, lighting, "
    "mood or camera. Do not guess a name. Do not use the word 'presence'. If something is "
    "not visible, leave it out rather than inventing it."
)

#: Asked when an answer arrived without any clothing in it, which is the half a shot cannot
#: invent: measured on a real reference, the read spent everything on hair and face and the
#: plan then invented a different outfit in every clip.
CAST_WARDROBE_FOLLOWUP = (
    "You left out the clothing. Answer again in one short paragraph, and this time start "
    "with the WARDROBE: every garment from top to bottom with its exact colour, fabric and "
    "fit, then footwear, then jewellery. Only after that, at most one short clause each, the "
    "apparent gender presentation, age range, skin, hair and face."
)

#: Longest appearance kept. A 2K reference warrants detail, but this sentence travels
#: into every shot that shows the character, so it is bounded rather than unbounded.
LOOK_LIMIT = 900

#: The pixel size the reference is sent at. 768 is enough to caption a picture; it is
#: not enough to see a fabric or an eyebrow. 1536 keeps a 2K render's own detail.
LOOK_IMAGE_MAX_SIZE = 1536

#: Answer tokens. The reasoning is paid for out of ``CAST_LOOK_THINKING_BUDGET``, which is
#: added on top, so this is the whole appearance rather than the appearance minus thinking.
CAST_LOOK_MAX_TOKENS = 320

#: Reasoning budget offered before the answer. A thinking model given only 560 total spent
#: all of it on reasoning and returned nothing, so the two budgets are separate.
CAST_LOOK_THINKING_BUDGET = 1400

_LOOK_KEYS = ("character_name", "role", "filename", "path")


def _reference_name(reference: Any) -> str:
    """The name a reference is filed under, with any `` (S1)`` cue removed."""

    if not isinstance(reference, dict):
        return ""
    for key in _LOOK_KEYS:
        value = str(reference.get(key) or "").strip()
        if not value:
            continue
        if key in ("filename", "path"):
            value = os.path.splitext(os.path.basename(value))[0]
        value = value.split(" (")[0].strip()
        if value:
            return value
    return ""


def _is_identity(reference: Any) -> bool:
    if not isinstance(reference, dict):
        return False
    if str(reference.get("type") or "image").lower() != "image":
        return False
    return str(reference.get("image_intent") or "identity").lower() == "identity"


def references_for_look(references: Optional[Iterable[Any]]) -> list[dict]:
    """The identity pictures worth describing, in manifest order, one per name."""

    seen: set[str] = set()
    kept: list[dict] = []
    for reference in references or ():
        if not _is_identity(reference):
            continue
        path = str(reference.get("path") or "")
        if not path or not os.path.isfile(path):
            continue
        name = _reference_name(reference)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        kept.append(reference)
    return kept


def vision_status() -> dict:
    """Whether a picture can actually be shown to the model right now."""

    try:
        from services import llm_service

        return {
            "loaded": bool(llm_service.is_loaded()),
            "available": bool(llm_service.vision_available()),
            "model": str(getattr(llm_service, "_model_id", "") or ""),
        }
    except Exception:  # noqa: BLE001 - status must never break a request
        return {"loaded": False, "available": False, "model": ""}


def _accepts_images(generate: Callable[..., str]) -> bool:
    """Whether this callable can carry an image at all.

    The app's own entry point was a wrapper with three positional parameters
    (``prompt, system_prompt, max_new_tokens``), so every vision call was made with the
    picture silently dropped and the model described a photo it never saw. A callable
    that cannot take ``image_paths`` is not a vision callable, and saying so beats asking
    a text model to look at nothing.
    """

    try:
        signature = inspect.signature(generate)
    except (TypeError, ValueError):
        return True
    parameters = signature.parameters
    if "image_paths" in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def appearance_from_reference(
    image_path: str,
    *,
    generate: Optional[Callable[..., str]] = None,
) -> str:
    """One dense appearance line read from ``image_path``, or ``""``."""

    if not image_path or not os.path.isfile(image_path):
        return ""
    try:
        if generate is None:
            from services import llm_service

            generate = llm_service.generate
        if not _accepts_images(generate):
            print(
                "[CastVision] The text generator cannot carry an image (no image_paths "
                "parameter), so the reference was not read. Fix the caller; do not ask a "
                "text model to describe a picture."
            )
            return ""
        # A thinking model spends its budget on the reasoning before it writes anything.
        # Measured on Strata (a Qwen3.8 thinking model): 560 tokens produced 2,279 chars of
        # reasoning, empty content and finish=length, so every reference read came back
        # blank and the appearance was discarded. The budget has to hold both, and a model
        # that still answers nothing gets one more attempt with twice the room.
        budgets = (CAST_LOOK_THINKING_BUDGET, CAST_LOOK_THINKING_BUDGET * 2)
        answer = ""
        for attempt, thinking_budget in enumerate(budgets):
            try:
                answer = generate(
                    prompt=CAST_LOOK_PROMPT + " Do not reason. Answer with the description only.",
                    system_prompt=(
                        "You describe people in photographs for a film continuity sheet. You "
                        "answer with observed detail only: one paragraph, no headers, no "
                        "lists, no commentary."
                    ),
                    max_new_tokens=CAST_LOOK_MAX_TOKENS,
                    thinking_budget=thinking_budget,
                    temperature=0.15,
                    image_paths=[image_path],
                    image_max_size=LOOK_IMAGE_MAX_SIZE,
                )
            except Exception as exc:  # noqa: BLE001 - a look is an enhancement, never a blocker
                print(f"[CastVision] Could not read {os.path.basename(image_path)}: {exc}")
                return ""
            if str(answer or "").strip():
                break
            if attempt == 0:
                print(
                    "[CastVision] The model answered nothing for "
                    f"{os.path.basename(image_path)} (all tokens went to reasoning); "
                    f"retrying with a larger budget."
                )
        # Wardrobe is the half a shot cannot invent: without it the plan dresses the person
        # differently in every clip. Measured on a real reference, the read spent its whole
        # answer on hair and face and stopped before naming a single garment.
        if str(answer or "").strip() and not _names_wardrobe(answer):
            print(
                f"[CastVision] {os.path.basename(image_path)}: no clothing in the read "
                "(hair and face only); asking again for the wardrobe."
            )
            try:
                retry = generate(
                    prompt=CAST_WARDROBE_FOLLOWUP,
                    system_prompt=(
                        "You describe people in photographs for a film continuity sheet. "
                        "Observed detail only, one paragraph."
                    ),
                    max_new_tokens=CAST_LOOK_MAX_TOKENS,
                    thinking_budget=budgets[0],
                    temperature=0.15,
                    image_paths=[image_path],
                    image_max_size=LOOK_IMAGE_MAX_SIZE,
                )
            except Exception:  # noqa: BLE001
                retry = ""
            if str(retry or "").strip() and _names_wardrobe(retry):
                answer = f"{_clean(answer)} {_clean(retry)}".strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[CastVision] Could not read {os.path.basename(image_path)}: {exc}")
        return ""
    return _clean(answer)


def gender_of(look: str) -> str:
    """``"woman"``, ``"man"`` or ``""`` from an appearance read, when it states one."""

    text = str(look or "").casefold()
    if not text:
        return ""
    woman = re.search(r"\b(woman|female|girl|lady|she/her)\b", text)
    man = re.search(r"\b(man|male|boy|gentleman|he/him)\b", text)
    if woman and not man:
        return "woman"
    if man and not woman:
        return "man"
    return ""


def narrator_gender(brief: str, looks: Optional[dict] = None) -> str:
    """``"woman"``/``"man"`` for the presenter, from her own read or from the brief.

    The planner writes a narrator it cannot see. Measured on a real project the brief named
    Anita and her reference picture was a woman, but the plan described "the narrator" as
    male in every shot, because nothing in the deterministic layer ever stated a gender.
    The appearance read states it first (a picture is authoritative); the CHARACTERS row is
    the fallback, since a brief that describes its cast says "young woman" there.
    """

    from .scene_policy import narrator_name

    name = narrator_name(brief)
    if not name:
        return ""
    if isinstance(looks, dict):
        for key, look in looks.items():
            if str(key).casefold().strip() == name.casefold():
                gender = gender_of(look)
                if gender:
                    return gender
    try:
        from .brief_assistant import _cast_rows
    except Exception:  # noqa: BLE001
        return ""
    for label, body in _cast_rows(brief).items():
        bare = re.sub(r"\s*\([^)]{1,16}\)\s*$", "", label).strip()
        if bare.casefold() == name.casefold() or same_name(bare, name):
            gender = gender_of(body)
            if gender:
                return gender
    return ""


def _names_wardrobe(text: str) -> bool:
    """Whether a read actually says what the person wears."""

    return bool(_GARMENT_RE.search(str(text or "")))


_GARMENT_RE = re.compile(
    r"\b(shirt|blouse|top|tee|t-shirt|tunic|dress|gown|skirt|trousers|pants|jeans|shorts|"
    r"suit|jacket|coat|blazer|cardigan|sweater|jumper|hoodie|vest|waistcoat|robe|robes|"
    r"cloak|cape|shawl|scarf|headscarf|veil|apron|uniform|armour|armor|chainmail|tunic|"
    r"belt|sash|bra|underwear|lingerie|stockings|tights|socks|boots?|shoes?|heels?|"
    r"sandals?|slippers?|trainers|sneakers|gloves|hat|cap|helmet|crown|tiara|necklace|"
    r"earrings?|bracelet|watch|glasses|sunglasses|ring)\b",
    re.IGNORECASE,
)


def _clean(answer: Any) -> str:
    """Keep the observable half of a model answer, or nothing."""

    text = " ".join(str(answer or "").split())
    if not text:
        return ""
    # Some models prefix a label or wrap the line in quotes.
    for prefix in ("character sheet:", "appearance:", "description:", "the person is "):
        if text.casefold().startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.strip(" \"'`*#-—")
    if len(text) < 12:
        return ""
    # The prompt asks for what is visible; a model that instead narrates the scene has
    # answered a different question, and that answer must not become continuity.
    lowered = text.casefold()
    if lowered.startswith(("this image", "the image", "in this picture", "i cannot", "i can't")):
        return ""
    if "presence" in lowered or "appears in" in lowered:
        return ""
    if len(text) > LOOK_LIMIT:
        # A thinking model can be cut off by the token budget mid-sentence. Ending the
        # continuity line at the last complete clause is better than a dangling "and a".
        clipped = text[:LOOK_LIMIT]
        for separator in (". ", "; ", ", "):
            cut = clipped.rfind(separator)
            if cut >= LOOK_LIMIT * 0.6:
                return clipped[:cut + 1].strip()
        return clipped.strip()
    return text


_RAW_SPEAKER_RE = re.compile(r"(?i)^\W*(?:speaker[_\s-]?)?(\d+)\W*$")


def canonical_speaker_label(value: Any) -> str:
    """A speaker identifier in the ``(S1)`` form the compiler parses, or ``""``.

    The transcript is diarized by pyannote, which names its voices ``SPEAKER_00``,
    ``SPEAKER_01``; Maestro numbers them by first appearance. A raw id that reached the
    brief was copied into the lock verbatim, and a lock whose cue is not ``(Sn)`` does not
    parse at all — measured on a real project the whole STRUCTURE came back with
    ``<Subject 1> (SPEAKER_00) is ALWAYS Anita.`` and every check downstream then reported
    "no SUBJECT LOCK the compiler can parse".

    ``SPEAKER_00`` is the first voice, so its number is one higher than the suffix only
    when the project numbers from 1; the mapping is therefore done by position elsewhere
    (``canonical_speaker_labels``). Here a bare number is read as a suffix, which is what
    the only unambiguous case is.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.fullmatch(r"\(?\s*S\s*(\d+)\s*\)?", raw, re.I)
    if match:
        return f"S{int(match.group(1))}"
    match = _RAW_SPEAKER_RE.match(raw)
    if match:
        number = int(match.group(1))
        # pyannote counts from 00; Maestro labels from S1.
        return f"S{number + 1}"
    return ""


def canonical_speaker_labels(speakers: Optional[Iterable[Any]]) -> list[str]:
    """The speaker list in first-appearance order, as ``["S1", "S2"]``.

    Position is the authority: Maestro numbers voices in the order they first speak, not
    by their pyannote suffix, so a project whose first voice is ``SPEAKER_03`` still calls
    it ``S1``. Already-canonical labels keep their number.
    """

    labels: list[str] = []
    used: set[int] = set()
    pending: list[int] = []
    for position, raw in enumerate(speakers or (), start=1):
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.fullmatch(r"\(?\s*S\s*(\d+)\s*\)?", text, re.I)
        if match:
            number = int(match.group(1))
            labels.append(f"S{number}")
            used.add(number)
            continue
        labels.append("")
        pending.append(position - 1)
    next_number = 1
    for position in pending:
        while next_number in used:
            next_number += 1
        labels[position] = f"S{next_number}"
        used.add(next_number)
        next_number += 1
    return labels


def _name_key(name: str) -> str:
    """A name reduced to what makes two spellings the same person."""

    text = re.sub(r"\(\s*S\s*\d+\s*\)", " ", str(name or ""), flags=re.I)
    return " ".join(re.findall(r"[a-záéíóúüñ0-9]+", text.casefold()))


def same_name(left: str, right: str) -> bool:
    """Whether two spellings name the same character.

    A reference role is authored by the person and the brief is written by the model, and
    they disagree on a letter often enough to matter: measured on a real project the
    picture was filed ``Goliat`` and the brief said ``Goliath``, so the appearance was
    applied to a row that did not exist and David's picture went on describing Goliath.
    One spelling extra, one letter different, or one being a leading part of the other is
    the same person. Two different names ("David" and "Saul") never match.
    """

    a, b = _name_key(left), _name_key(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if a.replace(" ", "") == b.replace(" ", ""):
        return True
    # "Goliath" / "Goliat": one extra character in the longer one.
    if len(a) >= 5 and len(b) >= 5 and (
        abs(len(a) - len(b)) <= 1
        and _one_edit_apart(a, b)
    ):
        return True
    # "Abuela Rosa" / "Rosa": a reference often drops the honorific.
    a_words, b_words = a.split(), b.split()
    if len(a_words) > len(b_words) and a_words[-len(b_words):] == b_words and len(b) >= 4:
        return True
    if len(b_words) > len(a_words) and b_words[-len(a_words):] == a_words and len(a) >= 4:
        return True
    return False


def _one_edit_apart(left: str, right: str) -> bool:
    """True when a single insertion, deletion or substitution separates the two."""

    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(1 for a, b in zip(left, right) if a != b) <= 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1:] == shorter:
            return True
    return False


def match_name(name: str, candidates: Iterable[str]) -> str:
    """The candidate that names the same character as ``name``, or ``""``."""

    for candidate in candidates:
        if same_name(name, candidate):
            return str(candidate)
    return ""


def speaker_cue_for(name: str, references: Optional[Iterable[Any]]) -> str:
    """The ``(Sx)`` an identity reference states beside ``name``, or ``""``.

    A reference role is authored as ``"Anita (S1)"``. That cue is the only thing that
    ties a picture to the voice on the audio track, and it was never reaching the brief:
    the plan described Anita with no ``(S1)``, so the compiler had to guess which
    participant spoke and every shot re-derived the narrator.
    """

    wanted = str(name or "").strip()
    if not wanted:
        return ""
    for reference in references or ():
        if not isinstance(reference, dict):
            continue
        role = str(reference.get("role") or reference.get("character_name") or "")
        label = _reference_name(reference)
        if not same_name(label, wanted):
            continue
        match = re.search(r"\(\s*S\s*(\d+)\s*\)", role, re.I)
        if match:
            return f"S{int(match.group(1))}"
    return ""


def reference_roster(
    references: Optional[Iterable[Any]],
    looks: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Every visual reference, in manifest order, with the person it belongs to.

    The manifest is the one authoritative list of what the model is given, and the brief
    never mentioned it: a project supplied three pictures, the text named Anita once with
    no cue and David and Goliath not at all, so the picture numbers meant nothing to the
    planner and it invented faces for people it already had.

    ``number`` is manifest order, which is the order H3 receives them in. The compiler
    renumbers per shot after filtering, so this is a roster for the writer, not a
    promise about any one shot's numbering.
    """

    roster: list[dict] = []
    seen: set[str] = set()
    for position, reference in enumerate(references or (), start=1):
        if not isinstance(reference, dict):
            continue
        if str(reference.get("type") or "image").lower() != "image":
            continue
        label = _reference_name(reference)
        intent = str(reference.get("image_intent") or "identity").lower()
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        role = str(reference.get("role") or "")
        cue_match = re.search(r"\(\s*S\s*(\d+)\s*\)", role, re.I)
        roster.append({
            "number": position,
            "name": label,
            "cue": f"S{int(cue_match.group(1))}" if cue_match else "",
            "intent": intent,
            "look": (looks or {}).get(label, ""),
        })
    return roster


def roster_block(roster: Optional[list[dict]]) -> str:
    """The ``REFERENCES:`` section: which picture is whom, and never to invent them."""

    if not roster:
        return ""
    lines = [
        "REFERENCES:",
        "Every person below already has a supplied picture. Never invent their face, "
        "hair, wardrobe or build: write them as the picture shows them, and name each "
        "one exactly as written here (with its speaker cue when it has one). The "
        "<Picture N> number is what the renderer binds that person to; it is assigned by "
        "the pipeline, so never renumber it and never write one yourself.",
    ]
    for item in roster:
        cue = f" ({item['cue']})" if item.get("cue") else ""
        number = item.get("number")
        label = f"<Picture {number}> " if number else ""
        detail = f" — {item['look']}" if item.get("look") else ""
        lines.append(
            f"{label}{item['name']}{cue} — {item['intent']} reference{detail}"
        )
    return "\n".join(lines)


def picture_map(pictures: Optional[list[dict]]) -> str:
    """``<Picture 1> is Anita (S1).`` — the binding the renderer will actually use.

    H3 maps every supplied picture to a Subject and states that the Subject appears in
    the shot, so the picture a person is bound to is part of what the shot means. Written
    per shot, after the references are filtered for that shot, so the numbers are the ones
    the compiler will assign rather than the manifest's.
    """

    if not pictures:
        return ""
    lines: list[str] = []
    for index, item in enumerate(pictures, start=1):
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "image").lower() != "image":
            continue
        name = _reference_name(item)
        if not name:
            continue
        cue_match = re.search(
            r"\(\s*S\s*(\d+)\s*\)", str(item.get("role") or ""), re.I
        )
        cue = f" (S{int(cue_match.group(1))})" if cue_match else ""
        lines.append(f"<Picture {index}> is {name}{cue}.")
    if not lines:
        return ""
    return (
        "PICTURES IN THIS SHOT (bind these numbers; never write a <Picture N> tag):\n"
        + "\n".join(f"- {line}" for line in lines)
    )


def apply_reference_roster(
    brief: str,
    references: Optional[Iterable[Any]],
    looks: Optional[dict[str, str]] = None,
) -> str:
    """Add the cue to every name a reference labels, and append the roster.

    Two mechanical edits, both things the code needs and neither changing an idea:

    * ``<Subject 1> is ALWAYS Anita.`` becomes ``<Subject 1> (S1) is ALWAYS Anita.``
      when a picture labels her ``Anita (S1)``, and her CHARACTERS row becomes
      ``Anita (S1): …`` — that cue is what binds the Subject to the voice.
    * a ``REFERENCES:`` section listing the pictures in manifest order.

    Idempotent: running it twice does not double a cue or a section.
    """

    roster = reference_roster(references, looks)
    text = str(brief or "")
    if not roster:
        return text
    # The brief may spell a person differently from the manifest ("Goliath" for a
    # picture filed as "Goliat"). One character with two spellings reads as two people,
    # so the roster adopts the spelling the text already uses.
    from .brief_assistant import _cast_rows

    brief_names = [
        name for name in _cast_rows(text) if not re.search(r"\(\s*S\s*\d+\s*\)", name, re.I)
    ]
    for item in roster:
        spelled = match_name(item["name"], brief_names)
        if spelled:
            item["name"] = spelled
    cued = [item for item in roster if item.get("cue")]
    if cued:
        out: list[str] = []
        lock_line = re.compile(
            r"^([ \t]*(?:[-*][ \t]*)?<\s*Subject\s+(\d+)\s*>)(?!\s*\()"
            r"([ \t]*(?:is\s+ALWAYS|es\s+SIEMPRE)[ \t]+)(.*)$",
            re.I,
        )
        row_line = re.compile(r"^([ \t]*(?:[-*][ \t]*)?)([^:\n]{2,60}?)(?!\s*\()([ \t]*:\s*.*)$")
        for line in text.splitlines():
            match = lock_line.match(line)
            if match:
                named = match.group(4)
                for item in cued:
                    if re.search(rf"\b{re.escape(item['name'])}\b", named, re.I) or same_name(
                        item["name"], named.split(".")[0]
                    ):
                        line = f"{match.group(1)} ({item['cue']}) {match.group(3).lstrip()}{named}"
                        break
                out.append(line)
                continue
            match = row_line.match(line)
            if match:
                name = " ".join(match.group(2).split())
                if "(" in name:
                    out.append(line)
                    continue
                for item in cued:
                    if same_name(item["name"], name):
                        line = f"{match.group(1)}{name} ({item['cue']}){match.group(3)}"
                        break
            out.append(line)
        text = "\n".join(out)
    # Drop an older roster so the section never stacks up.
    text = re.sub(r"(?ims)^[ \t]*REFERENCES\s*:.*?(?=^[ \t]*[A-Z][A-Z0-9 /_\-()]{2,50}\s*:\s*|\Z)", "", text)
    text = text.rstrip()
    block = roster_block(roster)
    return f"{text}\n\n{block}\n" if text else f"{block}\n"


def cast_descriptions(
    references: Optional[Iterable[Any]],
    *,
    generate: Optional[Callable[..., str]] = None,
    limit: int = 6,
) -> dict[str, str]:
    """``{"Anita": "<appearance read from her picture>", ...}``.

    Capped at ``limit`` because each one is a vision call on the same GPU the render is
    about to use, and a film has a handful of identities, not dozens.
    """

    looks: dict[str, str] = {}
    for reference in references_for_look(references)[: max(0, int(limit))]:
        name = _reference_name(reference)
        look = appearance_from_reference(str(reference.get("path") or ""), generate=generate)
        if name and look:
            looks[name] = look
    return looks


def looks_block(
    looks: Optional[dict[str, str]], names: Optional[Iterable[str]] = None, limit: int = 1400,
) -> str:
    """The continuity lines to carry into a shot, for the named characters only.

    Naming more than the shot contains is how one character's wardrobe used to reach
    every clip, so ``names`` filters: an appearance belongs to the shot that shows the
    person. ``limit`` bounds the whole block, because a detailed read of three characters
    would otherwise spend the shot's prompt budget on wardrobe.
    """

    if not looks:
        return ""
    if names is None:
        wanted = list(looks)
    else:
        wanted = []
        for name in names:
            match = next(
                (key for key in looks if key.casefold() == str(name).casefold()), None
            )
            if match and match not in wanted:
                wanted.append(match)
    lines = [f"{name}: {looks[name]}" for name in wanted if looks.get(name)]
    if not lines:
        return ""
    header = "HOW THEY LOOK (taken from their reference picture, keep it exactly):\n"
    budget = max(200, int(limit)) - len(header)
    kept: list[str] = []
    for line in lines:
        room = budget - sum(len(item) + 3 for item in kept)
        if room < 60:
            break
        kept.append(f"- {line[:room]}")
    if not kept:
        return ""
    return header + "\n".join(kept)


def apply_cast_descriptions(brief: str, looks: Optional[dict[str, str]]) -> str:
    """Put the reads from the reference pictures into the brief's CHARACTERS section.

    A row that already describes how the person looks is the writer's, and is kept: the
    picture is evidence for a gap, not a replacement for a choice. A row that says when
    someone appears instead ("David: presence in the biblical action stages") holds no
    look at all, and that is the one this repairs; a name with no row at all is added.
    """

    if not looks:
        return str(brief or "")
    from .brief_assistant import _describes_appearance, _sections

    lines = str(brief or "").splitlines()
    start = next(
        (
            index for index, line in enumerate(lines)
            if re.match(r"^\s*CHARACTERS?\b[^\n:]{0,40}:\s*$", line, re.I)
        ),
        None,
    )
    if start is None:
        lines.extend(["", "CHARACTERS:"])
        start = len(lines) - 1
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^\s*[A-Z][A-Z0-9 /_\-()]{2,50}\s*(?:\([^)]*\))?\s*:", lines[index]):
            end = index
            break

    # A name the model wrote twice ("David" in CHARACTERS and again in PRESENCES) is one
    # character with two rows, one of which says nothing about how he looks. Collect the
    # best existing description per name first, then write one row.
    sections = _sections(str(brief or ""))
    existing: dict[str, str] = {}
    for _section, body in sections.items():
        for line in body.splitlines():
            match = re.match(r"^\s*(?:[-*]\s*)?([^:\n]{2,50}?)\s*:\s*(.+)$", line)
            if not match:
                continue
            name = " ".join(match.group(1).split())
            body_text = " ".join(match.group(2).split())
            key = _name_key(name)
            if _describes_appearance(body_text) or key not in existing:
                if _describes_appearance(body_text) or not _describes_appearance(
                    existing.get(key, "")
                ):
                    existing[key] = body_text

    kept: list[str] = []
    seen: set[str] = set()
    for line in lines[start + 1:end]:
        if not line.strip():
            continue
        match = re.match(r"^\s*(?:[-*]\s*)?([^:\n]{2,50}?)\s*:\s*(.*)$", line)
        if not match:
            kept.append(line)
            continue
        name = " ".join(match.group(1).split())
        current = " ".join(match.group(2).split())
        key = _name_key(name)
        if key in seen:
            continue
        seen.add(key)
        look_key = next(
            (looks_name for looks_name in looks if same_name(looks_name, name)), None
        )
        best = existing.get(key, current)
        if look_key is not None and not _describes_appearance(best):
            best = looks[look_key]
        kept.append(f"{name}: {best}")
    for name, look in looks.items():
        already = [
            row.split(":", 1)[0].strip() for row in kept if ":" in row
        ]
        if _name_key(name) in seen or any(same_name(name, row) for row in already):
            continue
        # The brief may already spell this person differently ("Goliath" for a picture
        # filed as "Goliat"); one character with two spellings reads as two people.
        from .brief_assistant import _cast_rows

        spelled = match_name(name, list(_cast_rows(str(brief or "")))) or name
        kept.append(f"{spelled}: {look}")

    rebuilt = lines[: start + 1] + [""] + kept
    tail = lines[end:]
    if tail:
        rebuilt.append("")
        rebuilt.extend(tail)
    return "\n".join(rebuilt)
