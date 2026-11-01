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

import os
import re
from typing import Any, Callable, Iterable, Optional

__all__ = [
    "CAST_LOOK_PROMPT",
    "appearance_from_reference",
    "apply_cast_descriptions",
    "cast_descriptions",
    "looks_block",
    "references_for_look",
]

#: Asked of the vision model, per identity picture. It is deliberately narrow: hair,
#: face, skin, eyes, what is worn top to bottom. Anything wider invites the model to
#: describe the pose, the background or the mood, and those are the shot's business, not
#: the character's. The picture is a character sheet; the answer must read like one.
CAST_LOOK_PROMPT = (
    "Describe only the person in this picture, as a character sheet for a film crew.\n"
    "Give, in one dense line and in this order: age range; build; skin tone; hair colour, "
    "hair length and how it is worn (loose, tied back, braided, under a headscarf); eye "
    "colour; facial hair if any; then clothing from top to bottom with colours and "
    "materials; then footwear; then jewellery or accessories.\n"
    "Do not describe the background, the pose, the lighting, the mood or the camera. "
    "Do not guess a name. Do not use the word 'presence'. If something is not visible, "
    "leave it out rather than inventing it."
)

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
        answer = generate(
            prompt=CAST_LOOK_PROMPT,
            system_prompt=(
                "You describe people in photographs for a film continuity sheet. You "
                "answer with one line of observed detail and nothing else."
            ),
            max_new_tokens=200,
            temperature=0.2,
            image_paths=[image_path],
        )
    except Exception as exc:  # noqa: BLE001 - a look is an enhancement, never a blocker
        print(f"[CastVision] Could not read {os.path.basename(image_path)}: {exc}")
        return ""
    return _clean(answer)


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
    return text[:400]


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


def looks_block(looks: Optional[dict[str, str]], names: Optional[Iterable[str]] = None) -> str:
    """The continuity lines to carry into a shot, for the named characters only.

    Naming more than the shot contains is how one character's wardrobe used to reach
    every clip, so ``names`` filters: an appearance belongs to the shot that shows the
    person.
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
    return "HOW THEY LOOK (taken from their reference picture, keep it exactly):\n" + "\n".join(
        f"- {line}" for line in lines
    )


def apply_cast_descriptions(brief: str, looks: Optional[dict[str, str]]) -> str:
    """Put the reads from the reference pictures into the brief's CHARACTERS section.

    A row that already describes how the person looks is the writer's, and is kept: the
    picture is evidence for a gap, not a replacement for a choice. A row that says when
    someone appears instead ("David: presence in the biblical action stages") holds no
    look at all, and that is the one this repairs; a name with no row at all is added.
    """

    if not looks:
        return str(brief or "")
    from .brief_assistant import _describes_appearance

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
        key = next((key for key in looks if key.casefold() == name.casefold()), None)
        seen.add(name.casefold())
        if key is None or _describes_appearance(current):
            kept.append(f"{name}: {current}")
        else:
            kept.append(f"{name}: {looks[key]}")
    for name, look in looks.items():
        if name.casefold() not in seen:
            kept.append(f"{name}: {look}")

    rebuilt = lines[: start + 1] + [""] + kept
    tail = lines[end:]
    if tail:
        rebuilt.append("")
        rebuilt.extend(tail)
    return "\n".join(rebuilt)
