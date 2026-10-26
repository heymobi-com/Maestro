"""Fish Drama 3 voice references are bounded to a clean cloning window."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

try:
    import numpy as np
    import soundfile as sf

    from models.TTS.fish_audio.pipeline import (
        MAX_REFERENCE_SECONDS,
        bounded_reference,
    )
    RUNTIME_AVAILABLE = True
except ImportError:  # pragma: no cover - the app venv always ships these
    RUNTIME_AVAILABLE = False

SAMPLE_RATE = 44100


@unittest.skipUnless(RUNTIME_AVAILABLE, "sounderfile and numpy are required")
class FishReferenceWindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="maestro-fish-ref-"))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _write(self, name: str, seconds: float, channels: int = 1) -> Path:
        path = self.directory / name
        shape = (int(SAMPLE_RATE * seconds), channels) if channels > 1 else int(SAMPLE_RATE * seconds)
        sf.write(str(path), np.zeros(shape, dtype="float32"), SAMPLE_RATE, subtype="PCM_16")
        return path

    def test_a_short_reference_is_used_exactly_as_uploaded(self):
        short = self._write("clean.wav", 12)

        self.assertEqual(bounded_reference(short), Path(os.path.abspath(str(short))))

    def test_a_long_reference_keeps_only_the_cloning_window(self):
        """A 97-second upload muffled the clone and starved the speech budget."""

        long_reference = self._write("interview.wav", 97)

        bounded = bounded_reference(long_reference)

        self.assertNotEqual(Path(str(bounded)), long_reference)
        info = sf.info(str(bounded))
        self.assertAlmostEqual(info.duration, MAX_REFERENCE_SECONDS, places=2)
        self.assertEqual(info.samplerate, SAMPLE_RATE)

    def test_stereo_references_keep_their_channels(self):
        stereo = self._write("stereo.wav", 60, channels=2)

        bounded = bounded_reference(stereo)

        self.assertEqual(sf.info(str(bounded)).channels, 2)

    def test_the_trimmed_window_is_reused_across_runs(self):
        long_reference = self._write("again.wav", 45)

        first = bounded_reference(long_reference)
        second = bounded_reference(long_reference)

        self.assertEqual(first, second)

    def test_a_custom_window_is_honoured(self):
        long_reference = self._write("custom.wav", 20)

        bounded = bounded_reference(long_reference, max_seconds=5)

        self.assertAlmostEqual(sf.info(str(bounded)).duration, 5, places=2)

    def test_the_transcript_and_the_codes_see_the_same_file(self):
        """Trimming must happen before both, or a transcript describes audio
        the model never receives."""

        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "models" / "TTS" / "fish_audio" / "pipeline.py"
        ).read_text(encoding="utf-8")

        guides_line = source.index("guides = [bounded_reference(guide) for guide in guides]")
        transcripts_line = source.index("self._reference_transcripts(guides, alt_prompt)")
        codes_line = source.index("self._reference_codes(guide,")
        self.assertLess(guides_line, transcripts_line)
        self.assertLess(guides_line, codes_line)


if __name__ == "__main__":
    unittest.main()
