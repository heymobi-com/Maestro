"""Speak a Director script with an installed speech engine.

A Director project can send a written script to a video model, but then the video
model is the voice: 32 kHz, approximated words, and background prose read aloud.
The alternative was for the user to leave Director, generate the episode in
Studio, and come back to attach it. This module builds that episode from the
script itself, so the bridge is one request.

It does not implement speech. It reuses an installed model's own defaults, its own
voice-reference mode and the pipeline's existing child-job runner, and only decides
the things that are not a model's business: which speakers the script has, which
voice each of them uses, and what text each of them says.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

# Every request this module makes carries this marker. A baseline lookup skips it,
# so a request built from a baseline is never itself used as one.
BASELINE_SKIP_MARKER = "_tts_episode"

# The label convention every shipped speech model accepts for its numbered voice
# slots: see docs/TTS-Characters.md.
SPEAKER_LABEL = "Speaker {index}"

# Long-form multi-speaker engines first: a podcast is minutes of continuous
# two-voice speech, and these declare `speech_longform`. The order only breaks
# ties between engines that are actually installed.
DEFAULT_ENGINE_PREFERENCE = (
    "fish_drama3",
    "kugelaudio",
    "qwen3_tts_base",
    "index_tts2",
    "dramabox",
    "scenema_audio",
    "chatterbox",
)

# ``<d>[Spanish] ...</d>`` fixes the spoken language for a *video* model. A
# speech engine reads punctuation, not markup, so the tag would be pronounced.
_DIRECTIVE = re.compile(r"</?d>", re.IGNORECASE)
_LANGUAGE_TAG = re.compile(r"^\s*\[[^\]\n]{1,40}\]\s*")

WORDS_PER_SECOND = 2.8
# Measured with fish_drama3: a two-line script needed 17 s where 2.8 words per
# second predicted 5. An engine that treats the duration as a ceiling stops at
# EOS, so over-estimating costs nothing while under-estimating truncates.
MAXIMUM_DURATION_PACE = 1.8
MAXIMUM_DURATION_SLACK = 1.4


def spoken_rows(lines: Any) -> list[dict[str, str]]:
    """The lines an engine should speak: speaker + text, empty rows dropped."""
    rows: list[dict[str, str]] = []
    if not isinstance(lines, (list, tuple)):
        return rows
    for raw in lines:
        if not isinstance(raw, Mapping):
            continue
        text = _clean_line(str(raw.get("text") or ""))
        if not text:
            continue
        rows.append({"speaker": str(raw.get("speaker") or "").strip(), "text": text})
    return rows


def _clean_line(text: str) -> str:
    cleaned = _DIRECTIVE.sub("", text)
    cleaned = _LANGUAGE_TAG.sub("", cleaned)
    return cleaned.strip()


def speaker_slots(rows: Sequence[Mapping[str, str]]) -> dict[str, int]:
    """Map each scripted speaker onto a numbered voice slot, in first-use order."""
    slots: dict[str, int] = {}
    for row in rows:
        speaker = str(row.get("speaker") or "").strip()
        if speaker and speaker not in slots:
            slots[speaker] = len(slots) + 1
    return slots


def voice_map(voices: Any) -> dict[str, str]:
    """The scripted speaker -> voice reference path the user bound."""
    mapped: dict[str, str] = {}
    if not isinstance(voices, (list, tuple)):
        return mapped
    for raw in voices:
        if not isinstance(raw, Mapping):
            continue
        speaker = str(raw.get("speaker") or "").strip()
        path = str(raw.get("path") or "").strip()
        if speaker and path:
            mapped[speaker] = path
    return mapped


def build_prompt(
    rows: Sequence[Mapping[str, str]],
    max_voices: int = 2,
    voices: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """The dialogue prompt and the voice references it uses, in slot order.

    A speaker with a bound voice gets the next slot and keeps it; a speaker with
    none shares the last slot, so no line is lost and no slot is invented. The
    engine learns who speaks what from the numbered label, which is the convention
    every shipped speech model shares.
    """
    voices = voices or {}
    limit = max(1, int(max_voices or 1))
    slots: dict[str, int] = {}
    slot_paths: list[str] = []
    for row in rows:
        speaker = str(row.get("speaker") or "").strip()
        if not speaker or speaker in slots or speaker not in voices:
            continue
        path = voices[speaker]
        # Two speakers sharing one saved voice share one slot: sending the same
        # reference twice gives the engine nothing extra but costs a slot.
        existing = next(
            (index + 1 for index, used in enumerate(slot_paths) if used == path),
            None,
        )
        if existing is not None:
            slots[speaker] = existing
            continue
        if len(slot_paths) >= limit:
            continue
        slots[speaker] = len(slot_paths) + 1
        slot_paths.append(path)
    if not slot_paths:
        raise ValueError(
            "Every speaker needs a voice reference before the script can be spoken."
        )
    lines: list[str] = []
    for row in rows:
        speaker = str(row.get("speaker") or "").strip()
        index = slots.get(speaker, len(slot_paths))
        lines.append(f"{SPEAKER_LABEL.format(index=index)}: {row.get('text') or ''}")
    return "\n".join(lines), slot_paths


def estimate_seconds(text: str) -> float:
    """A duration for engines that need one instead of reading until silence."""
    words = len(re.findall(r"[\w']+", str(text or ""), flags=re.UNICODE))
    return max(10.0, round(words / WORDS_PER_SECOND * 1.25 + 3.0, 1))


def budget_seconds(text: str, model_def: Mapping[str, Any] | None) -> float:
    """The duration value this model needs for the whole script."""
    model_def = model_def or {}
    words = len(re.findall(r"[\w']+", str(text or ""), flags=re.UNICODE))
    generous = words / MAXIMUM_DURATION_PACE * MAXIMUM_DURATION_SLACK + 4.0
    if model_def.get("duration_is_maximum"):
        return float(min(3600, max(30, round(generous))))
    return max(10.0, round(generous))


def voice_mode(model_def: Mapping[str, Any] | None, voice_count: int) -> str:
    """The model's own audio-prompt mode for this many voice references.

    Each model declares the modes it accepts (``""``, ``A``, ``AB``, ``ABD``,
    ``AB2``...). Picking from that declaration keeps this engine-agnostic: two
    references on fish_drama3 means ``AB``, the mode every working dialogue
    generation in this workspace used.
    """
    if voice_count <= 0:
        return ""
    selection = list(
        ((model_def or {}).get("audio_prompt_type_sources") or {}).get("selection") or [""]
    )
    if voice_count == 1:
        return next(
            (mode for mode in selection if "A" in mode and "B" not in mode),
            "A",
        )
    letters = lambda mode: len(re.sub(r"[^A-Z]", "", mode))  # noqa: E731
    candidates = [
        mode for mode in selection if "B" in mode and letters(mode) >= voice_count
    ]
    if candidates:
        return min(candidates, key=letters)
    two_voice = next((mode for mode in selection if mode == "AB2"), "")
    if two_voice:
        return two_voice
    return "A"


def speech_models(wgp_module: Any) -> dict[str, dict]:
    """Every model that can voice two speakers, keyed by ``model_type``."""
    models: dict[str, dict] = {}
    for model_type in getattr(wgp_module, "displayed_model_types", []) or []:
        model_def = wgp_module.get_model_def(model_type) or {}
        if not (model_def.get("returns_audio") or model_def.get("audio_only")):
            continue
        if int(model_def.get("max_voice_count") or 0) < 2:
            continue
        models[str(model_type)] = model_def
    return models


def is_installed(wgp_module: Any, model_type: str) -> bool:
    """True when using this model needs no weights download."""
    try:
        return wgp_module.get_model_download_confirmation(model_type) is None
    except Exception:  # noqa: BLE001 - an unknown check must not pick a model
        return False


_TTS_KEY = re.compile(r"^_tts_")
_VOICE_BINDING_KEYS = (
    "audio_guide",
    "audio_guide2",
    "audio_guide3",
    "audio_guide4",
    "audio_guide5",
    "audio_guide6",
    # Reference transcripts belong to whichever references were attached then.
    "alt_prompt",
    "voice_clone_enabled",
    "voice_clone_mode",
    "voice_clone_refs",
)


def without_voice_bindings(params: Mapping[str, Any] | None) -> dict:
    """A baseline request with every previous speaker's voice taken out."""
    clean = dict(params or {})
    for key in list(clean):
        if _TTS_KEY.match(key) or key in _VOICE_BINDING_KEYS:
            clean.pop(key, None)
    return clean


def _records_voice_bindings(params: Mapping[str, Any]) -> bool:
    """True when the Studio bound actual voices into this request.

    The voice-slot UI records a character id or an uploaded filename per slot.
    A request that carries only ``audio_guide`` paths was not built there, so it
    is not evidence of a working speech configuration.
    """
    return any(
        str(params.get(f"_tts_character_id{index}") or "").strip()
        or str(params.get(f"_tts_voice_filename{index}") or "").strip()
        for index in range(1, 7)
    )


def speech_baseline(
    out_dir: str | Path | None,
    model_type: str,
    limit: int = 400,
) -> dict | None:
    """The newest request the Studio actually spoke this model with.

    Model defaults are not a working speech configuration: measured on
    fish_drama3 they carry ``top_k=0``, and a request built from them produced
    either no speech at all or 25 s of vowel noise, while the sixteen requests the
    Studio had run with that model's ``_int4`` variant - same voices, same text
    shape - spoke every line verbatim. Reusing one of those keeps this bridge a
    call to what already works instead of a second, guessed speech path.

    Only requests that recorded voice bindings qualify, so a request this module
    made (or any other script that passes reference paths directly) never becomes
    its own baseline.
    """
    if not out_dir:
        return None
    try:
        files = sorted(
            Path(out_dir).glob("*.meta.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:limit]
    except OSError:
        return None
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        params = record.get("params")
        if not isinstance(params, dict):
            continue
        if str(params.get("model_type") or "") != model_type:
            continue
        if params.get("_audio_sub_mode") != "speech":
            continue
        if params.get(BASELINE_SKIP_MARKER) or not _records_voice_bindings(params):
            continue
        return dict(params)
    return None


def resolve_engine(
    wgp_module: Any,
    requested: str = "",
    preference: Iterable[str] = DEFAULT_ENGINE_PREFERENCE,
    has_baseline: Callable[[str], bool] | None = None,
) -> tuple[str, dict]:
    """The engine to speak with: the requested one, or the best installed one.

    ``has_baseline`` says whether this workspace already holds a working speech
    request for a model. Those are tried first, because they are known to speak.
    """
    models = speech_models(wgp_module)
    requested = str(requested or "").strip()
    if requested and requested.lower() != "auto":
        if requested not in models:
            raise ValueError(
                f"{requested} cannot voice a two-speaker script. Choose a speech "
                "model with at least two voice slots."
            )
        if not is_installed(wgp_module, requested):
            raise ValueError(f"{requested} is not installed yet.")
        return requested, models[requested]
    ordered = [
        model_type
        for prefix in preference
        for model_type in models
        if model_type.startswith(prefix)
    ]
    ordered += [model_type for model_type in models if model_type not in ordered]
    for known_good in (True, False):
        for model_type in ordered:
            if not is_installed(wgp_module, model_type):
                continue
            if known_good and not (has_baseline and has_baseline(model_type)):
                continue
            return model_type, models[model_type]
    raise ValueError(
        "No installed speech model can voice two speakers. Install a multi-voice "
        "speech model in Studio > Audio > Speech, or attach an episode instead."
    )


def build_params(
    defaults: Mapping[str, Any] | None,
    model_type: str,
    model_def: Mapping[str, Any] | None,
    prompt: str,
    voice_paths: Sequence[str],
    settings_version: Any = None,
) -> dict:
    """The request for one speech generation, from the model's own defaults or
    from a recorded working request."""
    model_def = model_def or {}
    paths = [str(path) for path in voice_paths if path]
    params = without_voice_bindings(defaults)
    params.update({
        "model_type": model_type,
        "prompt": prompt,
        "_tts_original_prompt": prompt,
        "generation_mode": "audio",
        "_audio_sub_mode": "speech",
        "repeat_generation": 1,
        "audio_prompt_type": voice_mode(model_def, len(paths)),
        "_tts_voice_count": max(1, len(paths)),
        BASELINE_SKIP_MARKER: True,
    })
    for index, path in enumerate(paths):
        key = "audio_guide" if index == 0 else f"audio_guide{index + 1}"
        params[key] = path
    if settings_version is not None:
        params["settings_version"] = settings_version

    slider = model_def.get("duration_slider")
    if isinstance(slider, Mapping) and slider.get("default") is not None:
        # The model's own control, kept verbatim. On fish_drama3 that is 0, which
        # means its automatic budget, and every working dialogue generation in
        # this workspace used it: any positive value here made the language model
        # emit end-of-speech immediately and save a near-empty file.
        params["duration_seconds"] = slider["default"]
    else:
        params["duration_seconds"] = budget_seconds(prompt, model_def)

    for index in range(1, 6):
        params.setdefault(f"_tts_speaker_name{index}", "")
        params.setdefault(f"_tts_voice_filename{index}", "")
    return params
