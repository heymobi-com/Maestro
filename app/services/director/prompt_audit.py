"""Audit a whole project's shot prompts against the project's own contract.

Measured on magnifica-humanitas (177 shots, 4,421 characters of project text identical in
every one of them). The director's reading was "yo veo muchas inconsistencias en los
prompts generados automaticamente, esto crea dobles personajes, cruzamiento de asignacion
de voces, alucinaciones de h3", and the numbers agree:

  * 53 shots whose own field head gives a Subject to a different person than the project's
    SUBJECT LOCK does -- the shot is built on the wrong person, which is how the wrong
    character ends up speaking;
  * 20 shots that emit ``subject_definitions:`` twice, once per character, so the field
    that is supposed to declare the cast once is split in two;
  * 8 shots that hand the same spoken line to a different speaker than the shot next to
    them does;
  * 46 of 177 drafts carry the action labels (``Opening composition:``, ``Dialogue:``) at
    all, and where they exist their order varies, so the section that actually decides what
    happens on screen is written five different ways;
  * 46 drafts over 2,000 characters because the project text was pasted into the shot.

The project writes its own rules down, so this is a comparison and not an opinion: the
SUBJECT LOCK block ("``<Subject 1> es SIEMPRE Valeria``") is parsed from the project text
and every shot is checked against it. Findings are reported with a code and a severity --
``error`` means the shot will render wrong or cannot render, ``warning`` means it will
drift from its neighbours or is needlessly hard to edit -- and nothing here changes a
prompt. Repairing is a separate, opt-in step.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Sequence

from services.director.h3_dialogue import (
    h3_shared_line_speaker_problems,
    h3_subject_binding_problems,
    h3_unresolved_speaker_cue_problems,
)

# The project's subject lock, written by the director: "<Subject 1> es SIEMPRE Valeria."
_SUBJECT_LOCK_RE = re.compile(
    r"<Subject\s*(\d+)>\s*es\s+SIEMPRE\s+([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1"
    r"\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+)",
    re.IGNORECASE,
)

# One ``subject_definitions:`` field head. Emitting it twice is how a shot ends up
# declaring its cast in two blocks.
_SUBJECT_FIELD_RE = re.compile(r"(?mi)^[ \t]*subject_definitions[ \t]*:")

# The cast a shot declares in its own head, accepting the dialects found in the wild:
#   "<Subject 1> (S1): Valeria", "<Subject 1> is Valeria (S1):", "<Subject 1> = Valeria ="
_SUBJECT_HEAD_NAME_RE = re.compile(
    r"<Subject\s*(\d+)>\s*(?:(?:\(S\d+\)\s*[:=])|(?:is\s+)|(?:=\s*))"
    r"\s*([A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1\u00e1\u00e9\u00ed\u00f3\u00fa"
    r"\u00fc\u00f1]+)",
    re.IGNORECASE,
)

_SUBJECT_ANY_RE = re.compile(r"<Subject\s*(\d+)>", re.IGNORECASE)

# The labels that carry the shot's action, in the order the director expects them.
_ACTION_LABELS = ("Opening composition", "Dialogue:", "Canonical identity and world", "AMBIENTACION")

# Above this, a draft is carrying the project's shared text instead of its own action.
_DRAFT_PROJECT_TEXT_CHARS = 2000


def project_subject_lock(project_context: str) -> dict[int, str]:
    """The Subject-to-person map the project declares, from its own SUBJECT LOCK block."""

    lock: dict[int, str] = {}
    for match in _SUBJECT_LOCK_RE.finditer(str(project_context or "")):
        lock[int(match.group(1))] = match.group(2)
    return lock


def _findings_for_shot(
    clip: Any,
    index: int,
    clips: Sequence[Any],
    lock: dict[int, str],
    action_order: tuple[str, ...],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})

    prompt = str(clip.get("video_prompt") or "")
    draft = str(clip.get("_director_h3_source_prompt") or "")

    heads = _SUBJECT_FIELD_RE.findall(prompt)
    if len(heads) > 1:
        add(
            "subject-field-duplicated",
            "error",
            f"The prompt declares subject_definitions {len(heads)} times. The field "
            "declares the cast once, so a second head reads as a second cast and is how "
            "the same person ends up on screen twice.",
        )

    # First occurrence wins: the field head declares the cast at the top of the prompt and
    # is the one that decides who is on screen. The binding lines further down agree with
    # the project by construction, so letting a later match win would hide a swapped head.
    declared: dict[int, str] = {}
    for number, name in _SUBJECT_HEAD_NAME_RE.findall(prompt):
        declared.setdefault(int(number), name)
    # One finding per shot: a shot that swaps two Subjects produces two differences, and a
    # count of differences reads like a count of shots. The director needs the second.
    swaps = [
        (number, person, lock[number])
        for number, person in sorted(declared.items())
        if number in lock and person.casefold() != lock[number].casefold()
    ]
    if swaps:
        detail = "; ".join(
            f"<Subject {number}> as {person} (the lock says {expected})"
            for number, person, expected in swaps
        )
        add(
            "subject-swap",
            "error",
            f"The shot declares {detail}. The shot is built on the wrong person, so the "
            "voice that lands on screen is the other one.",
        )

    for number in sorted({int(n) for n in _SUBJECT_ANY_RE.findall(prompt)}):
        if lock and number not in lock:
            add(
                "subject-unlisted",
                "error",
                f"The prompt uses <Subject {number}>, which the project's SUBJECT LOCK does "
                f"not declare (it declares {sorted(lock)}). A subject nobody can identify "
                "is one the renderer has to invent.",
            )

    head_vs_binding = h3_subject_binding_problems(prompt)
    if head_vs_binding:
        add(
            "subject-head-vs-binding",
            "error",
            " ".join(head_vs_binding[:2])
            + (
                f" ({len(head_vs_binding) - 2} more in the same prompt)"
                if len(head_vs_binding) > 2 else ""
            ),
        )

    unnameable = h3_unresolved_speaker_cue_problems(prompt)
    if unnameable:
        add(
            "line-without-speaker",
            "error",
            f"{len(unnameable)} line(s) whose cue names nobody; the renderer refuses the "
            "shot. First: " + unnameable[0][:180],
        )

    crossing = h3_shared_line_speaker_problems(clips, index)
    if crossing:
        add(
            "line-speaker-crosses-shots",
            "warning",
            f"{len(crossing)} line(s) given to a different speaker than the neighbouring "
            "shot gives them. First: " + crossing[0][:180],
        )

    if draft:
        present = tuple(label for label in _ACTION_LABELS if label in draft)
        if not present:
            add(
                "action-section-missing",
                "warning",
                "The draft has no Opening composition/Dialogue section, so the shot's action "
                "is written as loose prose and nothing separates it from the project text. "
                "The compiler writes one into the prompt anyway, so this costs editing "
                "time, not rendering.",
            )
        elif present != action_order:
            add(
                "action-order-differs",
                "warning",
                f"The action sections appear as {list(present)}, while the rest of the "
                f"project writes them as {list(action_order)}. The same shot is read "
                "differently from one clip to the next.",
            )
        if len(draft) > _DRAFT_PROJECT_TEXT_CHARS:
            add(
                "project-text-in-draft",
                "warning",
                f"The draft is {len(draft)} characters, so it carries the project's shared "
                "text as well as the shot. The action is a few hundred characters and is "
                "lost inside the repetition.",
            )

    return findings


def audit_project_prompts(
    clips: Sequence[Any],
    *,
    project_context: str = "",
) -> dict[str, Any]:
    """Every shot checked against the project's contract. Changes nothing.

    ``project_context`` is the project's shared text (the clip's
    ``_director_project_context``); when it is omitted the first clip that has one is
    used, because that text is identical in every shot.
    """

    clips = list(clips or [])
    context = str(project_context or "")
    if not context:
        for clip in clips:
            context = str((clip or {}).get("_director_project_context") or "")
            if context:
                break
    lock = project_subject_lock(context)

    # The order the project writes its action sections in most of the time is the order a
    # shot is expected to follow; the deviations are what make editing heterogeneous.
    orders = Counter(
        tuple(label for label in _ACTION_LABELS if label in str(clip.get("_director_h3_source_prompt") or ""))
        for clip in clips
    )
    orders.pop((), None)
    dominant = orders.most_common(1)[0][0] if orders else ()
    if not dominant:
        dominant = ("Opening composition", "Dialogue:", "Canonical identity and world")

    shots: list[dict[str, Any]] = []
    totals: Counter = Counter()
    for index, clip in enumerate(clips):
        findings = _findings_for_shot(clip, index, clips, lock, dominant)
        if not findings:
            continue
        for finding in findings:
            totals[finding["code"]] += 1
        shots.append(
            {
                "shot": int(clip.get("index", index)) + 1,
                "index": index,
                "findings": findings,
            }
        )

    return {
        "shots": len(clips),
        "subject_lock": {number: name for number, name in sorted(lock.items())},
        "action_order": list(dominant),
        # Keyed by the labels joined, because a JSON object cannot be keyed by a list.
        "action_order_variants": {
            " > ".join(combo) if combo else "(no action section)": count
            for combo, count in orders.items()
        },
        "totals": dict(totals.most_common()),
        "findings": shots,
    }


def format_audit_report(audit: dict[str, Any], *, limit: int = 8) -> str:
    """The audit as a few lines that name the shot and the number, for a log or a terminal."""

    lines = [
        f"[Prompt audit] {audit.get('shots', 0)} shot(s) checked against the project contract.",
    ]
    lock = audit.get("subject_lock") or {}
    if lock:
        lines.append(
            "  subject lock: "
            + ", ".join(f"<Subject {number}> = {name}" for number, name in lock.items())
        )
    variants = audit.get("action_order_variants") or {}
    if len(variants) > 1:
        lines.append(
            f"  action sections are written {len(variants)} different way(s); the project's "
            f"own order is {audit.get('action_order')}"
        )
    totals = audit.get("totals") or {}
    if not totals:
        lines.append("  no inconsistencies found.")
        return "\n".join(lines)

    errors = 0
    for code, count in totals.items():
        severity = next(
            (
                finding["severity"]
                for shot in audit.get("findings", [])
                for finding in shot["findings"]
                if finding["code"] == code
            ),
            "warning",
        )
        errors += count if severity == "error" else 0
        lines.append(f"  {count:3d}  {code} ({severity})")
    lines.append(
        f"  -> {errors} shot(s) have error-level findings: these render wrong or cannot "
        "render. Nothing was changed."
    )
    for shot in (audit.get("findings") or [])[:limit]:
        first = shot["findings"][0]
        lines.append(f"  shot {shot['shot']}: {first['code']} - {first['message'][:150]}")
    return "\n".join(lines)
