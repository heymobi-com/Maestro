"""Read a recorded song and write down what it sings, for a cover.

A cover needs two things, and neither of them is the melody: the words, and a
style. The style is the user's; the words are the one part a machine has to
supply. This module supplies exactly that, reusing what the app already runs
elsewhere instead of adding an engine:

- **Vocal separation**: ``preprocessing.extract_vocals.get_vocals`` (RoFormer,
  already in ``ckpts/roformer``) — the same call ``/audio/analyze`` makes before
  it transcribes. Whisper on a full mix hears drums and reverb, not words.
- **Transcription**: Whisper **medium** through
  ``shared.deepy.transcription._load_whisper_medium``, the loader H3's dialogue
  pass uses. It is already on disk (``ckpts/whisper_medium``) and it is chosen
  over the faster-whisper *small* that the analysis uses because singing is
  harder than speech: melisma, stacked vocals and reverb are precisely what a
  small model mishears. The analysis' own comments say so ("mishearing sung
  vocals (reverb, layering, melisma)"). It runs on CPU by default, as H3 does,
  so a resident music model is never pushed out of VRAM.
- **Sections**: ``services.audio_analysis.analyze(transcribe=False)`` gives BPM,
  beats and labelled sections; ``llm_service.classify_song_sections`` then names
  them from the lyrics themselves. Its primary path is repetition detection —
  a repeated chorus is found without any LLM — and the LLM is only its fallback,
  so a machine with no LLM loaded still gets a real structure.
- **The glue, which is the only new part**: pair each transcribed line with the
  section it is sung in and emit ``[Verse]``/``[Chorus]`` labels in order.

Nothing is downloaded, no model is loaded unless a transcription is asked for,
and a song with no words answers ``[Instrumental]`` rather than inventing lines.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

# Module level on purpose: this file uses postponed annotations, so FastAPI
# resolves `request: Request` against the module globals. Imported inside
# register_routes it would be invisible there, the parameter would be read as a
# required query field, and every call would answer 422 before reading a body.
from fastapi import HTTPException, Request

SONG_LYRICS_ROUTE = "/api/v1/music/cover-lyrics"
INSTRUMENTAL_LYRICS = "[Instrumental]"


def _progress(step: str, detail: str = "") -> None:
    """Publish to the progress channel the app already polls.

    ``/api/v1/audio/analyze/status`` reports ``audio_analysis``'s phase dict, so
    a long cover read shows "Extracting vocals" / "Transcribing the vocals"
    instead of one silent wait, with no second status endpoint to maintain.
    """

    try:
        from services import audio_analysis
        set_progress = getattr(audio_analysis, "_set_progress", None)
        if callable(set_progress):
            set_progress(step, detail)
    except Exception:  # progress is a courtesy; never the reason a read fails
        pass


def _audio_for_whisper(audio_path: str):
    """16 kHz mono float32, the shape openai-whisper expects."""

    import librosa

    audio, _sample_rate = librosa.load(audio_path, sr=16000, mono=True)
    return audio


def _vocals_for(audio_path: str) -> Optional[str]:
    """The separated voice, reusing the stem when it is already on disk."""

    stem = os.path.join(
        os.path.dirname(audio_path),
        "vocals",
        f"{os.path.splitext(os.path.basename(audio_path))[0]}_vocals.wav",
    )
    if os.path.isfile(stem):
        return stem
    try:
        from preprocessing.extract_vocals import get_vocals

        _progress("extracting_vocals", "Separating the voice from the mix")
        return get_vocals(audio_path, stem)
    except Exception as error:  # a mix is worse to transcribe than a stem, not useless
        print(f"[SongCover] Vocal separation failed, transcribing the mix: {error}")
        return None


def transcribe_song(
    audio_path: str,
    *,
    language: Optional[str] = None,
    device: str = "cpu",
    initial_prompt: Optional[str] = None,
) -> dict:
    """Transcribe a song: timed lines plus the language Whisper decided on."""

    import torch
    from shared.deepy.transcription import _load_whisper_medium

    model = _load_whisper_medium(torch.device(device))
    audio = _audio_for_whisper(audio_path)
    result = model.transcribe(
        audio,
        language=language or None,
        initial_prompt=initial_prompt or None,
        word_timestamps=False,
        # A song repeats itself. Whisper's carry-over context turns a repeated
        # chorus into a loop of invented lines, so every 30s window is decoded
        # on its own.
        condition_on_previous_text=False,
        verbose=False,
    )
    segments = []
    for segment in result.get("segments", []) or []:
        text = " ".join(str(segment.get("text") or "").split())
        if not text:
            continue
        segments.append({
            "start": round(float(segment.get("start") or 0.0), 3),
            "end": round(float(segment.get("end") or 0.0), 3),
            "text": text,
        })
    return {"language": str(result.get("language") or ""), "segments": segments}


def section_index(segment: dict, sections: list) -> Optional[int]:
    """Which section a line is sung in, decided by its midpoint.

    The same rule the Director pipeline uses for script rows: sections are
    contiguous, so overlap alone would place a line that crosses a boundary in
    both of them. A line with no timing is left unplaced rather than filed at
    the start of the song, which is what a missing start would otherwise mean.
    """

    raw_start = segment.get("start")
    if raw_start is None or str(raw_start).strip() == "":
        return None
    try:
        start = float(raw_start)
        end = float(segment.get("end") if segment.get("end") is not None else start)
    except (TypeError, ValueError):
        return None
    middle = (start + end) / 2.0
    for index, section in enumerate(sections or []):
        try:
            if float(section["start"]) <= middle < float(section["end"]):
                return index
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _display_label(label: Any) -> str:
    return str(label or "").strip().lower().capitalize() or "Verse"


def labelled_lyrics(segments: list, sections: list) -> str:
    """The transcribed lines as a lyric sheet, with section labels in order.

    A section with no words is left out rather than filled with
    ``[Instrumental]``: a missed line is far more likely than a truly wordless
    verse, and telling the model a verse is instrumental would delete words the
    singer actually sings. The sections themselves travel in the answer, so the
    caller can see them.
    """

    lines: list[str] = []
    current: Optional[tuple] = None
    for segment in segments or []:
        text = " ".join(str(segment.get("text") or "").split())
        if not text:
            continue
        index = section_index(segment, sections)
        label = _display_label(sections[index].get("label")) if index is not None else "Verse"
        if current != (index, label):
            if lines:
                lines.append("")
            lines.append(f"[{label}]")
            current = (index, label)
        lines.append(text)
    return "\n".join(lines).strip()


def _section_labels(analysis: dict, lyrics: list, ensure_llm=None) -> list:
    """Name the analysis' sections from the lyrics, heuristically if need be.

    ``classify_song_sections`` finds a repeated chorus without any LLM when the
    transcription repeats cleanly, and measured on a real YuE2 song it did not:
    the second chorus came back as "con razón habla una unión" where the first
    said "por razón habrá una unión", and an exact-match repetition detector has
    nothing to match. That is why the app's own loader is passed in -- with the
    LLM available the labels are still named, and without it the audio's own
    labels are kept instead of the read failing.
    """

    sections = analysis.get("sections") or []
    fallback = [dict(section) for section in sections]
    try:
        from services import audio_analysis, llm_service

        if callable(ensure_llm) and not llm_service.is_loaded():
            ensure_llm()
        result = llm_service.classify_song_sections(
            sections=sections,
            lyrics=lyrics,
            duration=analysis.get("duration", 0),
        )
        structure = result.get("song_structure") or []
        if structure:
            # The same two steps the app's own classify-sections endpoint takes:
            # LLM boundaries when it produced a structure, relabelled sections
            # otherwise.
            updated = audio_analysis.replace_sections_with_structure(analysis, structure)
            return updated.get("sections") or fallback
        labels = result.get("labels") or []
        if len(labels) != len(sections):
            return fallback
        relabelled = audio_analysis.classify_sections_with_lyrics(analysis, labels)
        return relabelled.get("sections") or fallback
    except Exception as error:
        print(f"[SongCover] Section naming failed, keeping the audio labels: {error}")
        return fallback


def song_lyrics(
    audio_path: str,
    *,
    language: Optional[str] = None,
    device: str = "cpu",
    instrumental: bool = False,
    ensure_llm=None,
) -> dict:
    """Read a recorded song: labelled lyrics, structure, language and length."""

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    from services import audio_analysis

    _progress("loading_audio", "Reading the source song")
    analysis = audio_analysis.analyze(audio_path, transcribe=False)
    result = {
        "lyrics": INSTRUMENTAL_LYRICS,
        "language": "",
        "duration": analysis.get("duration"),
        "bpm": analysis.get("bpm"),
        "sections": analysis.get("sections") or [],
        "segments": [],
        "vocals_path": None,
        "source_path": audio_path,
    }
    if instrumental:
        return result

    vocals = _vocals_for(audio_path)
    result["vocals_path"] = vocals
    _progress("loading_transcription_model", "Loading Whisper medium (already on disk)")
    _progress("transcribing", "Transcribing the vocals")
    transcription = transcribe_song(vocals or audio_path, language=language, device=device)
    result["language"] = transcription["language"]
    result["segments"] = transcription["segments"]
    result["sections"] = _section_labels(analysis, transcription["segments"], ensure_llm)
    result["lyrics"] = labelled_lyrics(transcription["segments"], result["sections"]) or INSTRUMENTAL_LYRICS
    _progress("", "")
    print(
        f"[SongCover] {os.path.basename(audio_path)}: "
        f"{len(transcription['segments'])} lines, language {result['language'] or 'unknown'}, "
        f"{len(result['sections'])} sections"
    )
    return result


def register_routes(api, *, ensure_llm=None) -> None:
    """Register this module's routes; the app calls this once at startup.

    ``ensure_llm`` is the app's own model loader (``_ensure_llm_loaded``). It is
    passed in rather than imported because this module must stay importable --
    and testable -- without the application around it.
    """

    @api.post(SONG_LYRICS_ROUTE)
    async def song_cover_lyrics(request: Request):
        """Read a recorded song so it can be covered: lyrics, structure, length.

        Blocking work (separation, transcription, librosa) runs in a thread, so
        the API keeps answering the progress polls it is being asked for.
        """

        body = await request.json()
        audio_path = str(body.get("audio_path") or "").strip()
        if not audio_path:
            raise HTTPException(status_code=400, detail="audio_path is required")
        if not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail=f"Audio file not found: {audio_path}")
        language = str(body.get("language") or "").strip() or None
        try:
            return await asyncio.to_thread(
                song_lyrics,
                audio_path,
                language=language,
                device="cuda" if str(body.get("device") or "").lower() == "cuda" else "cpu",
                instrumental=bool(body.get("instrumental")),
                ensure_llm=ensure_llm,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
