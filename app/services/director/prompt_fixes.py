"""Corrections the assistant decides and the code applies.

The assistant's answer used to be load-bearing text: it had to re-type six thousand characters,
and any paraphrase was refused -- "an edit's FIND text is not in the current prompt, character for
character" -- so a correction loop read as "el asistente solo arroja errores, no sirve para nada"
next to a plain chat that understood the same prompt. The director's workflow is the opposite of
that: watch the render, say what is wrong, read a diagnosis, then have it corrected.

So the answer is split by what each half is good at. ``ANALYSIS`` and ``QUESTION`` stay free prose
-- that is the diagnosis. The correction is a short list of decisions in a fixed vocabulary
("the third line is Ricardo's", "that entry is Valeria", "the cast block is declared twice") and
this module applies them to the saved prompt, with the same verification the rest of the pipeline
uses: the spoken words, the six Context-IR fields and every byte outside the thing being changed
must survive, or nothing is written and the reason is reported.

    SET_SPEAKER 3 S2          line 3 is spoken by (S2)
    SET_SUBJECT 2 Ricardo     <Subject 2> is Ricardo (renumbers a swapped cast)
    NAME_SUBJECT 2 Ricardo    the entry for <Subject 2> never says who it is
    MERGE_SUBJECTS            subject_definitions is declared more than once
"""

from __future__ import annotations

import re
from typing import Any

from services.director.h3_dialogue import project_subject_lock
from services.director.prompt_audit import _SUBJECT_FIELD_RE, subject_block
from services.director.prompt_normalize import _SUBJECT_ENTRY_RE, normalize_subject_block


class PromptFixError(ValueError):
    """A fix the code refuses to apply, with the reason it refused."""


_SPOKEN_LINE_RE = re.compile(r"<d\b[^>]*>.*?</d>", re.S)
_SPEAKER_LABEL_RE = re.compile(r"\(\s*S\s*(\d+)\s*\)", re.IGNORECASE)
_SUBJECT_LABEL_RE = re.compile(r"<\s*Subject\s*(\d+)\s*>", re.IGNORECASE)

# What the renderer reads as the speaker of a line, so the fix rewrites the same place.
_CUE_WINDOW = 240

# The compiler's own phrasing where a participant's name should be.
_NAMELESS_ENTRY_RE = re.compile(r"(?i)facial\s*,\s*bodily")

# The Context-IR fields, in the order the contract expects them.
_H3_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_FIELD_ORDER_RE = re.compile(
    r"(?mi)^[ \t]*(" + "|".join(_H3_FIELDS) + r")[ \t]*:"
)

_FIX_LINE_RE = re.compile(
    r"(?i)^[ \t]*(?:\d+[.)][ \t]*)?(?:[-*+][ \t]*)?"
    r"(SET_SPEAKER|SET_SUBJECT|NAME_SUBJECT|MERGE_SUBJECTS|TAG_LINES|NO_FIX)\b[ \t]*(.*)$"
)


def _content(text: str) -> str:
    """The text with every speaker and Subject label removed, and blanks collapsed.

    This is the invariant for a label operation: whether a label is renumbered or inserted where
    there was none, deleting all of them has to leave the same prose. Blanking them with a marker
    instead (which is what the first version compared) refused an insertion, because the original
    had no label to blank.
    """

    stripped = re.sub(r"<\s*Subject\s*\d+\s*>", " ", str(text or ""))
    stripped = re.sub(r"\(\s*S\s*\d+\s*\)", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _refuse_if_anything_else_changed(before: str, after: str, what: str) -> None:
    """Everything except the labels being rewritten must be the same prose."""

    if _content(before) != _content(after):
        raise PromptFixError(f"{what} would change text other than a speaker or Subject label")
    if re.findall(r"<d\b", before) != re.findall(r"<d\b", after):
        raise PromptFixError(f"{what} would change the spoken lines")


def set_line_speaker(prompt: str, line_number: int, speaker: int) -> tuple[str, str]:
    """Give one spoken line a different speaker, by rewriting only its cue."""

    text = str(prompt or "")
    lines = list(_SPOKEN_LINE_RE.finditer(text))
    if not 1 <= int(line_number) <= len(lines):
        raise PromptFixError(
            f"the shot has {len(lines)} spoken line(s), so line {line_number} does not exist"
        )
    match = lines[int(line_number) - 1]
    previous_end = lines[int(line_number) - 2].end() if int(line_number) > 1 else 0
    cue_start = max(previous_end, match.start() - _CUE_WINDOW)
    # The same clause the renderer reads: since the last sentence break before the line.
    cue = text[cue_start:match.start()]
    boundary = max(
        cue.rfind("."), cue.rfind("!"), cue.rfind("?"), cue.rfind(";"), cue.rfind("\n"),
    ) + 1
    wanted = f"(S{int(speaker)})"
    labels = list(_SPEAKER_LABEL_RE.finditer(text, cue_start + boundary, match.start()))
    if labels:
        last = labels[-1]
        fixed = text[: last.start()] + wanted + text[last.end():]
    else:
        # Nothing to replace: state it where the renderer looks, next to the line.
        fixed = text[: match.start()] + f"{wanted} " + text[match.start():]
    _refuse_if_anything_else_changed(text, fixed, f"giving line {line_number} to {wanted}")
    return fixed, f"line {line_number} is now spoken by {wanted}"


def set_subject_person(prompt: str, subject: int, person: str) -> tuple[str, str]:
    """Make ``<Subject n>`` be ``person``, moving whoever held that number out of the way.

    A cast swap is one decision, not two: the director says "<Subject 2> is Ricardo" and the
    participant who was numbered 2 has to take the number Ricardo vacates, or two entries end up
    claiming the same Subject.
    """

    text = str(prompt or "")
    name = str(person or "").strip()
    if not name:
        raise PromptFixError("SET_SUBJECT needs the participant's name")
    wanted = int(subject)
    # Where each participant sits now, as the prompt itself declares it.
    current: dict[str, tuple[int, str]] = {}
    for match in _SUBJECT_ENTRY_RE.finditer(subject_block(text)):
        key = match.group(4).casefold()
        current.setdefault(key, (int(match.group(1)), match.group(4)))
    holder = next(
        (entry for entry in current.values() if entry[0] == wanted), None,
    )
    rules = [f"- <Subject {wanted}> es SIEMPRE {name}."]
    mine = current.get(name.casefold())
    if holder and holder[1].casefold() != name.casefold() and mine:
        rules.append(f"- <Subject {mine[0]}> es SIEMPRE {holder[1]}.")
    fixed = normalize_subject_block(text, "\n".join(rules))
    if fixed == text:
        if name.casefold() in subject_block(text).casefold():
            raise PromptFixError(f"the cast already numbers {name} as <Subject {wanted}>")
        raise PromptFixError(
            f"no cast entry names {name}, so nothing says which one <Subject {wanted}> is"
        )
    _refuse_if_anything_else_changed(text, fixed, f"making <Subject {wanted}> {name}")
    return fixed, f"<Subject {wanted}> is {name}"


def name_subject_entry(prompt: str, subject: int, person: str) -> tuple[str, str]:
    """Put a name on a cast entry that only says what to preserve."""

    text = str(prompt or "")
    name = str(person or "").strip()
    if not name:
        raise PromptFixError("NAME_SUBJECT needs the participant's name")
    block = subject_block(text)
    if not block:
        raise PromptFixError("the prompt has no subject_definitions block")
    entry = re.compile(rf"<\s*Subject\s*{int(subject)}\s*>", re.IGNORECASE).search(block)
    if not entry:
        raise PromptFixError(f"there is no entry for <Subject {subject}>")
    following = _SUBJECT_LABEL_RE.search(block, entry.end() + 1)
    entry_end = following.start() if following else len(block)
    entry_text = block[entry.start():entry_end]
    if not _NAMELESS_ENTRY_RE.search(entry_text):
        raise PromptFixError(f"the entry for <Subject {subject}> already describes a participant")
    colon = entry_text.find(":")
    if colon < 0:
        raise PromptFixError(f"the entry for <Subject {subject}> has no description to keep")
    head = entry_text[: colon + 1]
    rest = entry_text[colon + 1:]
    # Keep the original spacing exactly: the name goes after the colon, the boilerplate follows.
    spacer = rest[: len(rest) - len(rest.lstrip())] or " "
    fixed_entry = f"{head}{spacer}{name}, {rest.lstrip()}"
    fixed = text.replace(entry_text, fixed_entry, 1)
    # Naming a participant *is* new prose, so the invariant here is not "only labels moved":
    # it is that this one entry is the only thing that changed.
    if fixed.replace(fixed_entry, entry_text, 1) != text:
        raise PromptFixError(
            f"naming <Subject {subject}> would change text outside that cast entry"
        )
    if re.findall(r"<d\b", text) != re.findall(r"<d\b", fixed):
        raise PromptFixError("naming a participant would change the spoken lines")
    return fixed, f"<Subject {subject}> is {name} in the cast block"


def merge_subject_definitions(prompt: str) -> tuple[str, str]:
    """Fold extra ``subject_definitions`` heads into the first one.

    Only the extra labels are removed, never a line: the earlier attempt at this reformatting
    collapsed blank lines inside the block and could join two Context-IR fields, which is worse
    than the duplication it set out to fix.
    """

    text = str(prompt or "")
    matches = list(_SUBJECT_FIELD_RE.finditer(text))
    if len(matches) < 2:
        raise PromptFixError("subject_definitions is declared once, so there is nothing to merge")
    pieces: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        pieces.append(text[cursor:match.start()])
        if index == 0:
            pieces.append(match.group(0))
        cursor = match.end()
        # Drop the space that followed the removed label, so the entry still starts its line.
        if index and text[cursor:cursor + 1] in (" ", "\t"):
            cursor += 1
    pieces.append(text[cursor:])
    fixed = "".join(pieces)
    # Every line has to read the same with the heads taken out; only the labels may go. Comparing
    # the numbered skeleton was wrong, because dropping a label removes words rather than numbers.
    def _body(value: str) -> list[str]:
        # Blank lines are dropped and the rest stripped, because removing a label also removes the
        # space that followed it and can leave an empty line behind. What must not change is a
        # line's content.
        return [
            line.strip()
            for line in _SUBJECT_FIELD_RE.sub("", value).splitlines()
            if line.strip()
        ]

    if _body(text) != _body(fixed):
        raise PromptFixError("merging the cast block would change text other than its labels")
    if len(_SUBJECT_FIELD_RE.findall(fixed)) != 1:
        raise PromptFixError("merging the cast block did not leave exactly one head")

    def _fields(value: str) -> list[str]:
        # The distinct order, not the list: the duplicate head is exactly what is being removed,
        # so listing the fields verbatim proved the merge had changed them and refused every time.
        order: list[str] = []
        for name in _FIELD_ORDER_RE.findall(value):
            if name not in order:
                order.append(name)
        return order

    if _fields(text) != _fields(fixed):
        raise PromptFixError("merging the cast block would change the Context-IR fields")
    return fixed, f"the {len(matches) - 1} extra cast head(s) were folded into the first one"


def tag_line_cues_with_subjects(prompt: str) -> tuple[str, str]:
    """Put the speaker's identity tag beside every spoken line.

    A cue that carries only ``(Sx)`` does not name a character: the renderer's own message says
    "(Sx) labels only identify vocal-event order", and it resolves a line from a name or an
    explicit ``<Subject N>``. Whether ``(S1)`` happens to resolve depends on the alias table the
    request was built with -- measured on shots 139 and 140 of a real project, the same text was
    accepted by an offline check that had the reference labels and refused by the render (which
    had the characters' names), so the shot could not be generated at all.

    The tag is *added* beside the line rather than replacing the ``(Sx)``: the identity becomes
    explicit and the event-order label the project already wrote stays where it is. Only the
    labels move, so every other byte survives.
    """

    text = str(prompt or "")
    lines = list(_SPOKEN_LINE_RE.finditer(text))
    if not lines:
        raise PromptFixError("the shot has no spoken lines")
    pieces: list[str] = []
    cursor = 0
    notes: list[str] = []
    for index, match in enumerate(lines, start=1):
        previous_end = lines[index - 2].end() if index > 1 else 0
        cue_start = max(previous_end, match.start() - _CUE_WINDOW)
        cue = text[cue_start:match.start()]
        if _SUBJECT_LABEL_RE.search(cue):
            continue
        boundary = max(
            cue.rfind("."), cue.rfind("!"), cue.rfind("?"), cue.rfind(";"), cue.rfind("\n"),
        ) + 1
        labels = list(_SPEAKER_LABEL_RE.finditer(cue, boundary))
        if not labels:
            raise PromptFixError(
                f"line {index} carries no label to read its speaker from, so say who speaks it"
            )
        number = int(labels[-1].group(1))
        pieces.append(text[cursor:match.start()])
        pieces.append(f"<Subject {number}> ")
        cursor = match.start()
        notes.append(f"line {index} -> <Subject {number}>")
    if not notes:
        raise PromptFixError("every spoken line already carries its <Subject N>")
    fixed = "".join(pieces) + text[cursor:]
    _refuse_if_anything_else_changed(text, fixed, "tagging the spoken lines")
    return fixed, "; ".join(notes)


def parse_prompt_fixes(value: str) -> list[dict[str, Any]]:
    """The decisions in a ``FIXES:`` block, as operations this module can apply."""

    fixes: list[dict[str, Any]] = []
    for raw in str(value or "").splitlines():
        match = _FIX_LINE_RE.match(raw)
        if not match:
            continue
        operation = match.group(1).upper()
        arguments = match.group(2).strip()
        if operation == "NO_FIX":
            continue
        parts = [part for part in re.split(r"[,\s]+", arguments) if part]
        try:
            if operation == "SET_SPEAKER":
                fixes.append({
                    "operation": operation,
                    "line": int(parts[0]),
                    "speaker": int(re.sub(r"(?i)^s", "", parts[1])),
                })
            elif operation in ("SET_SUBJECT", "NAME_SUBJECT"):
                fixes.append({
                    "operation": operation,
                    "subject": int(parts[0]),
                    "person": " ".join(parts[1:]),
                })
            else:
                fixes.append({"operation": operation})
        except (IndexError, ValueError):
            raise PromptFixError(f"'{raw.strip()}' is not a usable {operation}")
    return fixes


def apply_prompt_fixes(
    prompt: str,
    fixes: list[dict[str, Any]],
    *,
    project_context: str = "",
) -> tuple[str, list[str]]:
    """Apply every decision in order. Raises ``PromptFixError`` on the first one it refuses."""

    if not fixes:
        return str(prompt or ""), []
    text = str(prompt or "")
    notes: list[str] = []
    for fix in fixes:
        operation = str(fix.get("operation") or "").upper()
        if operation == "SET_SPEAKER":
            text, note = set_line_speaker(text, fix["line"], fix["speaker"])
        elif operation == "SET_SUBJECT":
            text, note = set_subject_person(text, fix["subject"], fix["person"])
        elif operation == "NAME_SUBJECT":
            text, note = name_subject_entry(text, fix["subject"], fix["person"])
        elif operation == "MERGE_SUBJECTS":
            text, note = merge_subject_definitions(text)
        elif operation == "TAG_LINES":
            text, note = tag_line_cues_with_subjects(text)
        else:
            raise PromptFixError(f"'{operation}' is not an operation this code applies")
        notes.append(note)
    return text, notes
