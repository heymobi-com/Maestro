"""Assemble a podcast episode into one mastered MP3.

The speech engines produce the episode; this module only joins an optional
intro and outro, normalises loudness, and writes the MP3 container metadata
that a podcast player expects (cover art and ID3 chapters). It has no FastAPI
or model dependencies, so the command can be tested directly.
"""

from __future__ import annotations

import subprocess
from typing import Any, Mapping, Sequence

_METADATA_KEYS = ("title", "artist", "album", "genre", "comment")


def _clean(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def normalize_chapters(items: Sequence[Any] | None) -> list[tuple[float, str]]:
    """Return ``[(start_seconds, title), ...]`` in order, ignoring bad rows."""

    chapters: list[tuple[float, str]] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        try:
            start = max(0.0, float(item.get("start")))
        except (TypeError, ValueError):
            continue
        title = _clean(item.get("title"))
        if title:
            chapters.append((start, title))
    return sorted(chapters)


def build_ffmetadata(
    metadata: Mapping[str, Any] | None,
    chapters: Sequence[tuple[float, str]] | None,
    total_seconds: float,
) -> str:
    """Build the ffmpeg ``ffmetadata`` document, including ID3 chapters."""

    values = metadata or {}
    lines = [";FFMETADATA1"]
    for key in _METADATA_KEYS:
        value = _clean(values.get(key))
        if value:
            lines.append(f"{key}={value}")
    ordered = list(chapters or [])
    for index, (start, title) in enumerate(ordered):
        end = (
            ordered[index + 1][0]
            if index + 1 < len(ordered)
            else max(float(total_seconds), start + 0.5)
        )
        lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(start * 1000))}",
            f"END={int(round(max(end, start + 0.5) * 1000))}",
            f"title={_clean(title)}",
        ])
    return "\n".join(lines) + "\n"


def probe_duration(ffprobe_bin: str, path: str) -> float:
    """Duration in seconds, or 0 when the file cannot be probed."""

    try:
        probe = subprocess.run(
            [
                ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return max(0.0, float(probe.stdout.strip()))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 0.0


def build_command(
    ffmpeg_bin: str,
    audio_paths: Sequence[str],
    cover_path: str,
    metadata_path: str,
    out_path: str,
    *,
    loudness: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> list[str]:
    """The ffmpeg command that joins, normalises and writes the episode."""

    paths = [path for path in audio_paths if path]
    if not paths:
        raise ValueError("At least one audio path is required")

    inputs: list[str] = []
    for path in paths:
        inputs.extend(["-i", path])
    audio_count = len(paths)
    cover_index = audio_count if cover_path else -1
    if cover_path:
        inputs.extend(["-i", cover_path])
    metadata_index = audio_count + (1 if cover_path else 0)
    inputs.extend(["-f", "ffmetadata", "-i", metadata_path])

    filters: list[str] = []
    labels: list[str] = []
    for index in range(audio_count):
        label = f"a{index}"
        filters.append(
            f"[{index}:a]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")
    if audio_count > 1:
        filters.append(
            "".join(labels) + f"concat=n={audio_count}:v=0:a=1[joined]"
        )
        joined = "[joined]"
    else:
        joined = labels[0]
    if loudness:
        filters.append(f"{joined}loudnorm=I=-16:TP=-1.5:LRA=11[out]")
        out_label = "[out]"
    else:
        out_label = joined

    command = [
        ffmpeg_bin, "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", out_label,
        "-c:a", "libmp3lame", "-q:a", "2", "-ar", "48000", "-ac", "2",
        "-id3v2_version", "3",
        "-map_metadata", str(metadata_index),
        "-map_chapters", str(metadata_index),
    ]
    if cover_path:
        command.extend([
            "-map", f"{cover_index}:v",
            "-c:v", "mjpeg", "-disposition:v", "attached_pic",
        ])
    values = metadata or {}
    for key in _METADATA_KEYS:
        value = _clean(values.get(key))
        if value:
            command.extend(["-metadata", f"{key}={value}"])
    command.append(out_path)
    return command
