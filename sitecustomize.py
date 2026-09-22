"""Project-level startup shim for compatibility with pyannote 3.3 on torchaudio 2.10+.

pyannote 3.3 still expects a legacy torchaudio API surface (AudioMetaData,
info(), and list_audio_backends()) that was removed in newer torchaudio
releases. This shim restores the missing symbols as soon as Python starts,
which lets pyannote import reliably before the app-specific modules run.
"""

from __future__ import annotations

import sys


def _install_torchaudio_compatibility() -> None:
    try:
        import torchaudio
    except Exception:
        return

    if hasattr(torchaudio, "AudioMetaData") and hasattr(torchaudio, "info"):
        return

    try:
        import soundfile as sf
    except Exception:
        return

    class AudioMetaData(dict):
        def __init__(self, *, sample_rate, num_frames, num_channels, bits_per_sample, encoding=None, **kwargs):
            super().__init__(
                sample_rate=sample_rate,
                num_frames=num_frames,
                num_channels=num_channels,
                bits_per_sample=bits_per_sample,
                encoding=encoding,
                **kwargs,
            )

        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError as exc:
                raise AttributeError(name) from exc

        def __setattr__(self, name, value):
            self[name] = value

    def _list_audio_backends():
        return ["soundfile"]

    def _info(file_or_path, *, backend=None, **kwargs):
        if hasattr(file_or_path, "read"):
            handle = file_or_path
            pos = None
            try:
                pos = handle.tell()
            except (AttributeError, OSError):
                pos = None
            if pos is not None:
                handle.seek(0)
            info = sf.info(handle.name if hasattr(handle, "name") else handle)
            if pos is not None:
                handle.seek(pos)
        else:
            info = sf.info(file_or_path)

        bits = 16
        subtype = getattr(info, "subtype", "PCM_16")
        if "FLOAT" in subtype:
            bits = 32
        elif "DOUBLE" in subtype:
            bits = 64
        elif "PCM_" in subtype:
            try:
                bits = int(subtype.replace("PCM_", ""))
            except ValueError:
                bits = 16

        return AudioMetaData(
            sample_rate=int(info.samplerate),
            num_frames=int(info.frames),
            num_channels=int(info.channels),
            bits_per_sample=bits,
            encoding=info.subtype,
        )

    torchaudio.AudioMetaData = AudioMetaData
    torchaudio.list_audio_backends = _list_audio_backends
    torchaudio.info = _info


_install_torchaudio_compatibility()
