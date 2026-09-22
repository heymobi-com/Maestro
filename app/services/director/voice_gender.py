"""Acoustic voice-gender verification for Director speaker mappings.

Maestro never measured voice gender. The gender of a host came only from text
the user or the planner wrote, so a swapped or guessed name produced a video
where the wrong face carried the wrong voice and nothing could detect it.

This module measures the fundamental frequency (f0) of each diarized speaker
and compares it with the gender the project declares. It is deliberately
conservative: only a clear pitch separation against a declared gender is
reported, and the ambiguous 140-175 Hz band makes no claim at all.

Everything here is advisory. A conflict produces a warning, never a failure.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

_FEMALE_WORDS = (
    "female", "woman", "women", "girl", "lady", "madam",
    "femenin", "mujer", "senora", "chica", "dama",
)
_MALE_WORDS = (
    "male", "man", "men", "boy", "guy", "gentleman",
    "masculin", "hombre", "senor", "chico", "caballero",
)

_MALE_MAX_HZ = 140.0
_FEMALE_MIN_HZ = 175.0
_SAMPLE_RATE = 16000


def _normalized_words(value: Any) -> str:
    text = str(value or "").casefold()
    text = (
        text.replace("\u00e1", "a").replace("\u00e9", "e")
        .replace("\u00ed", "i").replace("\u00f3", "o").replace("\u00fa", "u")
        .replace("\u00f1", "n")
    )
    return text


def _gender_word_matches(text: Any) -> list[tuple[int, str]]:
    """Return ``(position, gender)`` for every gender word in ``text``.

    ``_normalized_words`` only swaps one character for another, so the offsets
    stay valid for the original string.
    """

    lowered = _normalized_words(text)
    found: list[tuple[int, str]] = []
    for word in _FEMALE_WORDS:
        for match in re.finditer(rf"(?<![a-z]){re.escape(word)}", lowered):
            found.append((match.start(), "female"))
    for word in _MALE_WORDS:
        for match in re.finditer(rf"(?<![a-z]){re.escape(word)}", lowered):
            found.append((match.start(), "male"))
    return sorted(found)


def _nearest_gender(text: Any, anchor: int) -> str:
    """Return the gender word closest to ``anchor``, or an empty string."""

    matches = _gender_word_matches(text)
    if not matches:
        return ""
    return min(matches, key=lambda item: abs(item[0] - anchor))[1]


def _gender_in_text(value: Any) -> str:
    """Return the declared gender of a short descriptor, if it states one."""

    text = _normalized_words(value)
    if not text:
        return ""
    for word in _FEMALE_WORDS:
        if re.search(rf"(?<![a-z]){re.escape(word)}", text):
            return "female"
    for word in _MALE_WORDS:
        if re.search(rf"(?<![a-z]){re.escape(word)}", text):
            return "male"
    return ""


def declared_gender_for_labels(
    concept_text: Any,
    speaker_mappings: Iterable[Any] | None,
    labels: Sequence[str],
) -> dict[str, str]:
    """Find the gender the project declares for each stable speaker label.

    Two sources are consulted, in order of authority: the user-authored
    speaker mapping name (``"woman in cream dress"``) and the surrounding text
    in the concept (``"S1 (Female) = Bela"``).
    """

    hints: dict[str, str] = {}
    for entry in speaker_mappings or []:
        if not isinstance(entry, Mapping):
            continue
        label = str(entry.get("speakerId") or entry.get("speaker_id") or "").strip()
        if not label:
            continue
        gender = _gender_in_text(entry.get("name", ""))
        if gender:
            hints[label] = gender

    text = str(concept_text or "")
    for label in labels:
        if label in hints:
            continue
        bare = label.strip("()")
        patterns = [re.escape(label)]
        if bare and bare != label:
            patterns.append(rf"(?<![A-Za-z0-9]){re.escape(bare)}(?![A-Za-z0-9])")
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                # A declaration can read "S2 (Male) = Rudo" (gender after the
                # label) or "<Subject 1> is the woman ... (S1)" (gender before
                # it), so look both ways. It must never leave the label's own
                # line: in a real project the " (S1)" sits at the END of its
                # line, and a fixed 160-char look-ahead reached the next line's
                # "is the man", which is nearer than its own "Mujer" and won.
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end < 0:
                    line_end = len(text)
                if line_end - line_start > 240:
                    line_start = max(line_start, match.start() - 120)
                    line_end = min(line_end, match.end() + 120)
                window = text[line_start:line_end]
                gender = _nearest_gender(window, match.start() - line_start)
                if gender:
                    hints[label] = gender
                    break
            if label in hints:
                break
    return hints


def estimate_gender_from_pitch(median_f0: Any) -> str:
    """Classify a median f0 into a gender only when it is unambiguous."""

    try:
        hertz = float(median_f0)
    except (TypeError, ValueError):
        return ""
    if hertz <= 0:
        return ""
    if hertz < _MALE_MAX_HZ:
        return "male"
    if hertz > _FEMALE_MIN_HZ:
        return "female"
    return ""


def speaker_turns(lyrics: Iterable[Any] | None) -> dict[str, list[dict[str, float]]]:
    """Group transcript lines per stable label, preserving timeline order.

    Accepts both shapes Maestro produces: the transcript's raw ``speaker`` ids
    (``SPEAKER_00``) and the diarization segments' already-stable
    ``speaker_id`` labels (``(S1)``).
    """

    from .speaker_binding import _CANONICAL_LABEL_RE, speaker_label_order

    order = speaker_label_order(lyrics)
    grouped: dict[str, list[dict[str, float]]] = {}
    for line in lyrics or []:
        if not isinstance(line, Mapping):
            continue
        raw = str(line.get("speaker") or line.get("speaker_id") or "").strip()
        label = order.get(raw.upper(), "")
        if not label and _CANONICAL_LABEL_RE.fullmatch(raw):
            label = raw
        if not label:
            continue
        try:
            start = float(line.get("start", 0.0) or 0.0)
            end = float(line.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        grouped.setdefault(label, []).append({"start": start, "end": end})
    return grouped


def measure_pitch_per_label(
    audio_path: str,
    lyrics: Iterable[Any] | None,
    *,
    max_seconds_per_label: float = 40.0,
    window_seconds: float = 4.0,
) -> dict[str, dict[str, float]]:
    """Measure the median f0 of each speaker from bounded audio slices."""

    import numpy as np
    import librosa
    import soundfile as sf

    grouped = speaker_turns(lyrics)
    if not grouped:
        return {}

    results: dict[str, dict[str, float]] = {}
    with sf.SoundFile(audio_path) as handle:
        native_rate = handle.samplerate
        for label, turns in grouped.items():
            budget = max_seconds_per_label
            frames: list[Any] = []
            analyzed = 0.0
            for turn in turns:
                if budget <= 0:
                    break
                length = min(window_seconds, turn["end"] - turn["start"], budget)
                if length < 1.5:
                    continue
                handle.seek(int(turn["start"] * native_rate))
                block = handle.read(
                    int(length * native_rate), dtype="float32", always_2d=True,
                )
                if block.size == 0:
                    continue
                mono = block.mean(axis=1)
                if native_rate != _SAMPLE_RATE:
                    mono = librosa.resample(
                        mono, orig_sr=native_rate, target_sr=_SAMPLE_RATE,
                    )
                if mono.size < 4000:
                    continue
                try:
                    f0 = librosa.yin(
                        mono, fmin=60, fmax=400, sr=_SAMPLE_RATE,
                    )
                except Exception:
                    continue
                f0 = f0[np.isfinite(f0)]
                if f0.size:
                    frames.append(f0)
                    analyzed += length
                budget -= length
            if not frames:
                continue
            every = np.concatenate(frames)
            results[label] = {
                "median_f0": float(np.median(every)),
                "p10_f0": float(np.percentile(every, 10)),
                "p90_f0": float(np.percentile(every, 90)),
                "voiced_frames": int(every.size),
                "seconds_analyzed": round(analyzed, 2),
            }
    return results


def verify_voice_genders(
    audio_path: str,
    lyrics: Iterable[Any] | None,
    *,
    speaker_mappings: Iterable[Any] | None = None,
    concept_text: Any = "",
    max_seconds_per_label: float = 40.0,
) -> tuple[dict[str, dict[str, float]], list[dict[str, str]]]:
    """Measure every speaker and report gender conflicts.

    Returns ``(measurements, conflicts)``. Both are advisory diagnostics; an
    empty result simply means no claim could be made.
    """

    measurements = measure_pitch_per_label(
        audio_path, lyrics, max_seconds_per_label=max_seconds_per_label,
    )
    if not measurements:
        return {}, []

    labels = sorted(measurements)
    declared = declared_gender_for_labels(concept_text, speaker_mappings, labels)
    conflicts: list[dict[str, str]] = []
    for label in labels:
        measured = estimate_gender_from_pitch(measurements[label]["median_f0"])
        if not measured:
            continue
        want = declared.get(label, "")
        if want and want != measured:
            conflicts.append({
                "label": label,
                "declared": want,
                "measured": measured,
                "median_f0": f"{measurements[label]['median_f0']:.1f}",
            })
    return measurements, conflicts


def describe_measurements(measurements: Mapping[str, Mapping[str, float]]) -> str:
    """One-line summary suitable for a pipeline log."""

    if not measurements:
        return "no speaker pitch could be measured"
    parts = []
    for label in sorted(measurements):
        row = measurements[label]
        leaning = estimate_gender_from_pitch(row.get("median_f0")) or "ambiguous"
        parts.append(f"{label}={row['median_f0']:.0f}Hz ({leaning})")
    return ", ".join(parts)
