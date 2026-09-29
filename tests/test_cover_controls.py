"""The Cover section: what it fills, where it hooks in, and what it costs.

The backend read is pinned by `test_song_cover.py`. This pins the half the user
actually touches: the panel that calls it, the fields it fills, the source it
shares with YuE2's own picker, and the two declared lines it costs in a file
upstream owns. A cover is only correct if the lyrics land where the model reads
them and the source lands where YuE2's validator looks for it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "ui/src/components/Sidebar/CoverControls.tsx"
MUSIC_CONTROLS = ROOT / "ui/src/components/Sidebar/MusicControls.tsx"
API = ROOT / "ui/src/api/songCover.ts"
BUDGET = ROOT / "scripts/upstream_seam_budget.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class CoverPanelTests(unittest.TestCase):
    def setUp(self):
        self.controls = _read(CONTROLS)
        self.api = _read(API)

    def test_it_reads_the_recording_through_our_own_endpoint(self):
        self.assertIn("readCoverLyrics({ audio_path: path })", self.controls)
        self.assertIn("from '../../api/songCover'", self.controls)
        self.assertIn("${BASE}/api/v1/music/cover-lyrics", self.api)
        # The server's reason is what names the unreadable file, but it is not
        # always a string: a validation error carries a list, and an unrouted
        # request is plain text. Both are read instead of collapsed.
        self.assertIn("typeof detail === 'string'", self.api)
        self.assertIn("Array.isArray(detail)", self.api)
        self.assertIn("restart the app so it loads it", self.api)

    def test_the_words_land_where_the_model_reads_them(self):
        # Lyrics are params.prompt: the same field the Lyrics editor writes and
        # the backend requires.
        self.assertIn("setParam('prompt', result.lyrics)", self.controls)

    def test_the_recording_is_the_source_the_pipeline_already_reads(self):
        # One source, not a second one: audio_prompt_type "A" + audio_guide is
        # what YuE2's own picker writes, and what ACE-Step's pipeline reads as
        # has_src_audio.
        self.assertIn("setParam(slot, uploaded.path)", self.controls)
        self.assertIn("if (!task) setParam('audio_prompt_type', 'A')", self.controls)
        self.assertIn("const task = String(params.audio_prompt_type || '')", self.controls)
        self.assertIn("from '../../api/client'", self.controls)
        self.assertIn("uploadAudio(file)", self.controls)

    def test_ace_step_gets_the_controls_its_cover_mode_reads(self):
        # The pipeline's own contract: use_ref = "A" in audio_prompt_type,
        # has_src_audio = use_ref and audio_guide, and audio_cover_strength =
        # audio_scale. Without the task letter the recording is ignored and the
        # cover silently becomes a new song.
        self.assertIn("modelOptions?.audio_prompt_type_sources", self.controls)
        self.assertIn("setParam('audio_prompt_type', event.target.value)", self.controls)
        self.assertIn("taskConfig?.labels?.[choice]", self.controls)
        self.assertIn("uploadInto(file, 'audio_guide2')", self.controls)
        self.assertIn("setParam('audio_scale', parseFloat(event.target.value))", self.controls)
        self.assertIn("modelOptions?.audio_scale_name", self.controls)
        self.assertIn("max={1}", self.controls)

    def test_the_tempo_of_the_source_travels_with_the_cover(self):
        # A cover that disagrees with its source about the beat is the first
        # thing the ear notices, so the measured BPM is handed over instead of
        # left to be guessed.
        self.assertIn("bpm: Math.round(result.bpm)", self.controls)
        # And the length: a ceiling for YuE2, the recording's own length for a
        # length-asked engine.
        self.assertIn(
            "yue2 ? Math.ceil(result.duration) + 10 : Math.round(result.duration)",
            self.controls,
        )

    def test_a_cover_is_armed_the_way_yue2_requires(self):
        # Direct generation cannot use a source song at all, and YuE2 refuses
        # "scoring a source song" outside a planning mode; the duration is a
        # ceiling, so the source's own length is the right one.
        self.assertIn("setParam('model_mode', 0)", self.controls)
        self.assertIn("Math.ceil(result.duration) + 10", self.controls)
        self.assertIn("modelOptions?.yue2_composition === true", self.controls)


class CoverSeamTests(unittest.TestCase):
    def test_the_music_panel_renders_our_section(self):
        panel = _read(MUSIC_CONTROLS)
        self.assertIn("import { CoverControls } from './CoverControls'", panel)
        self.assertIn("<CoverControls />", panel)

    def test_the_seam_is_declared_and_stays_two_lines(self):
        budget = json.loads(_read(BUDGET))
        self.assertEqual(budget["files"]["ui/src/components/Sidebar/MusicControls.tsx"], 2)
        # The reason travels with the number, like every other entry.
        self.assertIn("ui/src/components/Sidebar/MusicControls.tsx", budget["notes"])
        panel = _read(MUSIC_CONTROLS)
        # One import, one render; nothing else of ours may appear there.
        ours = [line for line in panel.splitlines() if "CoverControls" in line]
        self.assertEqual(len(ours), 2)


if __name__ == "__main__":
    unittest.main()
