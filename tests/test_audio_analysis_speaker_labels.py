import sys
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

_APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(_APP))

from services import audio_analysis  # noqa: E402


class TestTorchAudioCompatibility(unittest.TestCase):
    def test_torchaudio_compatibility_layer_sets_legacy_pyannote_symbols(self):
        audio_path = Path(__file__).resolve().parent / "tmp_test_torchaudio.wav"
        sample_rate = 16000
        duration = 0.2
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        audio = 0.3 * np.sin(2 * np.pi * 220 * t)
        sf.write(audio_path, audio, sample_rate)

        audio_analysis._ensure_torchaudio_compatibility()

        import torchaudio

        self.assertTrue(hasattr(torchaudio, "AudioMetaData"))
        self.assertTrue(hasattr(torchaudio, "info"))

        meta = torchaudio.info(str(audio_path))
        self.assertEqual(meta.sample_rate, sample_rate)
        self.assertGreater(meta.num_frames, 0)
        self.assertEqual(meta.num_channels, 1)

        audio_path.unlink(missing_ok=True)

    def test_diarized_speaker_ids_follow_maestro_h3_ordering(self):
        speaker_order = ["speaker_01", "speaker_00", "speaker_01", "speaker_02"]
        mapped = audio_analysis._speaker_label_order(speaker_order)

        self.assertEqual(mapped["SPEAKER_01"], "(S1)")
        self.assertEqual(mapped["SPEAKER_00"], "(S2)")
        self.assertEqual(mapped["SPEAKER_02"], "(S3)")

        self.assertEqual(audio_analysis._normalize_speaker_id("speaker_00", label_order=mapped), "(S2)")
        self.assertEqual(audio_analysis._normalize_speaker_id("speaker_01", label_order=mapped), "(S1)")
        self.assertEqual(audio_analysis._normalize_speaker_id("speaker_02", label_order=mapped), "(S3)")


if __name__ == "__main__":
    unittest.main()
