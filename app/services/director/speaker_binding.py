"""Deterministic speaker binding for audio-driven Director plans.

The shot planner is not a reliable source of per-line speaker identity. In one
real 150-clip project, 367 of 422 dialogue beats arrived **without**
``speaker_id``, and no subject carried ``character_id`` or ``speaker_name`` at
all. Because ``DialogueBeat.to_dict()`` omits an empty ``speaker_id``, that loss
is then persisted.

Without a per-line speaker, Maestro cannot know which host owns each line, so
every downstream ``(S1)``/``(S2)`` label was re-derived from the *position* of
the subject in a shot. A shot that lists only the man promotes him to ``(S1)``,
which puts the wrong face on the wrong voice.

The diarized transcript already contains the authoritative answer: each line
carries its raw pyannote speaker id (``SPEAKER_00``) and timestamps. This module
copies that truth onto the plan deterministically instead of hoping the planner
repeats it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

_H3_TAG_RE = re.compile(r"</?\s*d\s*>", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_SPACE_RE = re.compile(r"\s+")
_CANONICAL_LABEL_RE = re.compile(r"\(S\d+\)")
# A planner frequently writes the label INSIDE the spoken line, as
# "(S2): Claro, sí." or "S2. Claro, sí.". Left in place it makes the line
# unmatchable against the diarized transcript, which silently demoted the
# speaker to whichever voice dominated the window.
_LEADING_LABEL_RE = re.compile(r"^\s*\(?\s*S(\d+)\s*\)?\s*[:.\-\u2013]\s*", re.IGNORECASE)
# The same label also appears immediately after the opening <d> tag, as in
# "<d>(S2): Es una excelente pregunta".
_LABEL_AFTER_OPEN_TAG_RE = re.compile(
    r"^(\s*<\s*d\s*>\s*)\(?\s*S(\d+)\s*\)?\s*[:.\-\u2013]\s*",
    re.IGNORECASE,
)
# The same lines often still carry the H3 language tag, as in
# "<d>[Spanish] O sea, esa es...". The app treats a leading bracket as a
# language tag everywhere else, so match that behaviour here. Only a bare
# word is accepted, so a genuine "[Shot 2]" prefix is never mistaken for one.
_LEADING_LANG_RE = re.compile(
    r"^\s*\[\s*([A-Za-z][A-Za-z\u00c0-\u024f ]{1,23})\s*\]\s*",
)
# Minimum token overlap accepted as the same spoken line. Measured on a real
# project, true matches scored 0.75-0.94 while unrelated lines stayed below.
_FUZZY_MIN_SCORE = 0.72
_FUZZY_MIN_MARGIN = 0.12


def strip_speaker_prefix(value: Any) -> str:
    """Remove a speaker label the planner wrote at the start of a line.

    The label sits either at the very start or right after the opening ``<d>``
    tag; both spellings are removed while any wrapper is preserved.
    """

    text = _LEADING_LABEL_RE.sub("", str(value or ""))
    text = _LABEL_AFTER_OPEN_TAG_RE.sub(r"\1", text)
    return text.strip()


def _inline_speaker_number(value: Any) -> int:
    """Return the ``Sx`` number written inside a line, or 0 when absent."""

    text = str(value or "")
    leading = _LEADING_LABEL_RE.match(text)
    if leading:
        return int(leading.group(1))
    # Removing the tags promotes an inner label to the start of the string.
    promoted = _LEADING_LABEL_RE.match(_H3_TAG_RE.sub(" ", text))
    return int(promoted.group(1)) if promoted else 0


def normalize_line(value: Any) -> str:
    """Normalize a spoken line so transcript and plan text can be matched."""

    text = str(value or "")
    # Planned beats frequently keep the H3 wrapper they were transcribed with,
    # and the accents in these projects arrive as UTF-8 mojibake.
    text = _H3_TAG_RE.sub(" ", text)
    text = _LEADING_LANG_RE.sub(" ", text)
    text = strip_speaker_prefix(text)
    text = (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_ALNUM_RE.sub(" ", text.casefold())
    return _SPACE_RE.sub(" ", text).strip()


def _match_score(wanted: str, row_text: str) -> float:
    """Score how likely two normalized lines are the same spoken sentence."""

    if not wanted or not row_text:
        return 0.0
    if wanted == row_text:
        return 1.0
    if row_text.startswith(wanted) or wanted.startswith(row_text):
        return 0.95
    wanted_tokens = set(wanted.split())
    row_tokens = set(row_text.split())
    if not wanted_tokens or not row_tokens:
        return 0.0
    if wanted_tokens <= row_tokens or row_tokens <= wanted_tokens:
        return 0.9
    return len(wanted_tokens & row_tokens) / len(wanted_tokens | row_tokens)


def speaker_label_order(lyrics: Iterable[Any] | None) -> dict[str, str]:
    """Map raw diarization ids to stable ``(S1)``, ``(S2)`` labels.

    Mirrors ``audio_analysis._speaker_label_order``: first-seen order in the
    diarized timeline, which is the same rule the UI and the speech analysis
    already use.
    """

    order: dict[str, str] = {}
    for line in lyrics or []:
        if not isinstance(line, Mapping):
            continue
        raw = str(
            line.get("speaker") or line.get("speaker_id") or "",
        ).strip().upper()
        if not raw:
            continue
        if _CANONICAL_LABEL_RE.fullmatch(raw):
            # Already carries its stable label; never renumber it.
            continue
        if raw not in order:
            order[raw] = f"(S{len(order) + 1})"
    return order


def canonical_speaker_label(
    value: Any,
    order: Mapping[str, str] | None = None,
) -> str:
    """Normalize a speaker identifier to the canonical ``(S1)`` form.

    Saved plans mix ``(S1)`` with bare ``S2``, and either spelling fails to
    match the other downstream, which silently demotes the line to the
    positional default. Raw pyannote ids are resolved through ``order`` because
    Maestro numbers speakers by first appearance, not by their ``_00`` suffix.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if order and upper in order:
        return order[upper]
    match = re.fullmatch(r"\(?S(\d+)\)?", upper)
    if match:
        return f"(S{int(match.group(1))})"
    return raw


def _line_rows(
    lyrics: Iterable[Any] | None,
    order: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lyrics or []:
        if not isinstance(line, Mapping):
            continue
        raw = str(line.get("speaker") or line.get("speaker_id") or "").strip().upper()
        label = order.get(raw, "")
        if not label:
            continue
        try:
            start = float(line.get("start", 0.0) or 0.0)
            end = float(line.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        rows.append({
            "start": start,
            "end": end,
            "label": label,
            "text": normalize_line(line.get("text", "")),
        })
    return rows


def _window(row: Mapping[str, Any]) -> tuple[float, float]:
    try:
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", start) or start)
    except (TypeError, ValueError):
        return (0.0, 0.0)
    return (start, end)


def bind_dialogue_speakers(
    state_containers: Sequence[MutableMapping[str, Any]],
    windows: Sequence[Mapping[str, Any]],
    lyrics: Iterable[Any] | None,
) -> dict[str, int]:
    """Attach the diarized speaker to every dialogue beat, in place.

    ``state_containers`` are the clip-plan dictionaries that hold
    ``_director_dialogue_beats`` and ``_director_subjects_on_screen``.
    ``windows`` supplies each clip's audio window in the same order.

    Returns a small statistics dictionary so the caller can log the outcome.
    """

    order = speaker_label_order(lyrics)
    rows = _line_rows(lyrics, order)
    stats = {
        "beats_total": 0,
        "beats_bound": 0,
        "beats_fallback": 0,
        "beats_unresolved": 0,
        "beats_normalized": 0,
        "beats_corrected": 0,
        "beats_prefixed": 0,
        "subjects_relabelled": 0,
    }
    if not rows:
        return stats

    for index, container in enumerate(state_containers or []):
        beats = container.get("_director_dialogue_beats") or []
        if not beats:
            continue
        window = windows[index] if index < len(windows) else {}
        window_start, window_end = _window(window if isinstance(window, Mapping) else {})

        in_window = [
            row for row in rows
            if window_end > window_start
            and row["start"] < window_end
            and row["end"] > window_start
        ]
        if not in_window:
            in_window = rows

        dominant = ""
        if isinstance(window, Mapping):
            dominant = order.get(str(window.get("dominant_speaker") or "").strip().upper(), "")
        window_labels = {row["label"] for row in in_window}
        fallback_label = dominant or (
            next(iter(window_labels)) if len(window_labels) == 1 else ""
        )

        consumed: set[int] = set()
        for beat in beats:
            if not isinstance(beat, MutableMapping):
                continue
            stats["beats_total"] += 1
            # A label the planner wrote inside the line is not authoritative,
            # but it is a useful last resort when the transcript has no match.
            raw_spoken = str(beat.get("spoken_text") or "")
            inline_number = _inline_speaker_number(raw_spoken)
            prefixed_label = ""
            if inline_number:
                candidate = f"(S{inline_number})"
                # Only a label this project actually knows may be reused; a
                # stray "(S7)" must never reach the registry.
                if candidate in set(order.values()):
                    prefixed_label = candidate
            wanted = normalize_line(raw_spoken)
            if prefixed_label:
                stats["beats_prefixed"] += 1
                # Keep the persisted line clean: Maestro renders the speaker
                # itself, and a second label inside the block is exactly what
                # mis-assigned the voices downstream.
                beat["spoken_text"] = strip_speaker_prefix(raw_spoken)
                stats["beats_normalized"] += 1

            # The diarized transcript is the acoustic truth. Match against it
            # first so a wrong label already persisted in the plan, or the
            # window-dominant voice, can never override the real speaker.
            match_index = -1
            best_score = 0.0
            runner_up = 0.0
            best_label = ""
            if wanted:
                by_label: dict[str, float] = {}
                for row_index, row in enumerate(in_window):
                    if row_index in consumed:
                        continue
                    score = _match_score(wanted, row["text"])
                    if score > by_label.get(row["label"], 0.0):
                        by_label[row["label"]] = score
                ranked = sorted(by_label.items(), key=lambda item: -item[1])
                if ranked:
                    best_label, best_score = ranked[0]
                    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
                    for row_index, row in enumerate(in_window):
                        if row_index in consumed or row["label"] != best_label:
                            continue
                        if _match_score(wanted, row["text"]) >= best_score:
                            match_index = row_index
                            break

            existing = canonical_speaker_label(beat.get("speaker_id"), order)
            existing_is_canonical = bool(
                existing and _CANONICAL_LABEL_RE.fullmatch(existing)
            )
            ambiguous = (
                match_index >= 0
                and best_score < 1.0
                and runner_up >= _FUZZY_MIN_SCORE
                and best_score - runner_up < _FUZZY_MIN_MARGIN
            )
            # An approximate match that contradicts the label written on this
            # very line is more likely to have latched onto a neighbouring line
            # than to have found the truth. An exact match never gets here.
            prefix_beats_fuzzy = bool(
                prefixed_label
                and match_index >= 0
                and best_label
                and prefixed_label != best_label
                and best_score < 0.95
            )
            if (
                match_index >= 0
                and best_score >= _FUZZY_MIN_SCORE
                and not ambiguous
                and not prefix_beats_fuzzy
            ):
                consumed.add(match_index)
                label = best_label
                stats["beats_bound"] += 1
                if existing_is_canonical and existing != label:
                    # The persisted label contradicted the audio. Say so, and
                    # correct it: this is the wrong-voice-for-the-gender case.
                    stats["beats_corrected"] += 1
            elif prefix_beats_fuzzy:
                label = prefixed_label
                stats["beats_fallback"] += 1
            elif existing_is_canonical:
                if existing != beat.get("speaker_id"):
                    beat["speaker_id"] = existing
                    stats["beats_normalized"] += 1
                continue
            elif prefixed_label:
                label = prefixed_label
                stats["beats_fallback"] += 1
            elif fallback_label:
                label = fallback_label
                stats["beats_fallback"] += 1
            else:
                stats["beats_unresolved"] += 1
                # Never leave a non-canonical label behind to be re-derived.
                if not _CANONICAL_LABEL_RE.fullmatch(
                    str(beat.get("speaker_id") or "")
                ):
                    beat.pop("speaker_id", None)
                continue

            beat["speaker_id"] = label

    # Give the visible cast the same identity vocabulary as the beats so the
    # stable-label registry resolves it instead of guessing from list order.
    for index, container in enumerate(state_containers or []):
        beats = container.get("_director_dialogue_beats") or []
        labels = {
            str(beat.get("speaker_id") or "").strip()
            for beat in beats
            if isinstance(beat, Mapping) and beat.get("speaker_id")
        }
        labels.discard("")
        if not labels:
            continue
        subjects = container.get("_director_subjects_on_screen") or []
        if len(subjects) == 1 and len(labels) == 1:
            subject = subjects[0]
            if isinstance(subject, MutableMapping) and not subject.get("character_id"):
                subject["character_id"] = next(iter(labels))
                stats["subjects_relabelled"] += 1

    return stats


def cast_vocabulary(
    speaker_mappings: Iterable[Any] | None,
    lyrics: Iterable[Any] | None = None,
) -> dict[str, str]:
    """Return ``{stable_label: display_name}`` for this project.

    The user-authored mapping is authoritative. Any diarized speaker without a
    mapping entry is still listed, so the planner always has a complete and
    closed set of labels instead of inventing its own.
    """

    vocabulary: dict[str, str] = {}
    for entry in speaker_mappings or []:
        if not isinstance(entry, Mapping):
            continue
        label = canonical_speaker_label(
            entry.get("speakerId") or entry.get("speaker_id"),
        )
        if not label or not _CANONICAL_LABEL_RE.fullmatch(label):
            continue
        name = str(entry.get("name") or "").strip()
        vocabulary[label] = name or label
    for label in speaker_label_order(lyrics).values():
        vocabulary.setdefault(label, label)
    return vocabulary


def speaker_label_sort_key(label: Any) -> tuple[int, int | str]:
    match = re.fullmatch(r"\(S(\d+)\)", str(label or "").strip())
    if match:
        return (0, int(match.group(1)))
    return (1, str(label or ""))


def format_speaker(label: Any, name: Any = None) -> str:
    """Render the one spelling the planner is allowed to reuse."""

    clean_label = str(label or "").strip()
    clean_name = str(name or "").strip()
    if clean_label and clean_name and clean_name != clean_label:
        return f"{clean_label} {clean_name}"
    return clean_label or clean_name


def speaker_cast_block(
    vocabulary: Mapping[str, str] | None,
    genders: Mapping[str, str] | None = None,
) -> str:
    """Build the binding cast list injected into every planner prompt."""

    if not vocabulary:
        return ""
    genders = genders or {}
    lines: list[str] = []
    for label in sorted(vocabulary, key=speaker_label_sort_key):
        gender = str(genders.get(label) or "").strip()
        suffix = f" — {gender} voice" if gender else ""
        lines.append(f"  {format_speaker(label, vocabulary[label])}{suffix}")
    return "\n".join(lines)


SPEAKER_LABEL_RULES = """CANONICAL SPEAKER LABELS — BINDING, NEVER DEVIATE:
- The CAST list above is the complete and closed set of speakers for this project.
- dialogue_beats[].speaker_id MUST be exactly one of those labels, spelled identically, parentheses included.
- Never invent a label. No (S3), no "Subject 3", no "Speaker 2", no bare "S1" without parentheses.
- A label always belongs to the same person in every shot. Never renumber a speaker because they happen to speak first, last, or alone in a shot.
- When one person speaks alone in a shot, that person keeps their assigned label.
- Write every visible person as "<Subject N> (Sx), <visible description>", where (Sx) is that person's canonical label, and keep the pairing identical across all shots.
- Never write notes, reasoning, alternatives, or commentary inside any field. Every field contains only the requested value."""


def describe_cast(
    vocabulary: Mapping[str, str] | None,
    lyrics: Iterable[Any] | None = None,
) -> str:
    """One-line summary of the label vocabulary, for a pipeline log."""

    vocabulary = vocabulary or {}
    if not vocabulary:
        return "no speaker vocabulary available"
    order = speaker_label_order(lyrics)
    raw = ", ".join(f"{key}->{value}" for key, value in order.items()) or "none"
    labels = ", ".join(
        format_speaker(label, vocabulary[label])
        for label in sorted(vocabulary, key=speaker_label_sort_key)
    )
    return f"{labels} (raw diarization: {raw})"
