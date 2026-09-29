"""Lyrics are optional in Music mode: the app writes them before it submits.

The Music panel collects three texts but only two of them travel -- the Music
Caption (alt_prompt) and the Lyrics (prompt). "Describe your song" is a UI-only
input kept for the sidecar, so a user who described the song and pressed Generate
submitted an empty prompt, and the backend refuses a music request whose prompt is
empty with a bare "prompt is required", before any job exists. Measured in one
session: four Generate clicks (YuE2, then MiniMax-Music3) answered 400 and no song
was ever queued.

These tests pin the two files that carry the fix and the contract between them:
the store's single call, and the module that writes from the description, keeps
the written words in the fields the model actually reads, uses the instrumental
sentinel instead of inventing words, and answers with a reason when there is
nothing to write from. They read the source because the behaviour only exists as
these files agreeing, and dropping the seam during an upstream merge must fail a
test rather than lose the feature.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "ui/src/stores/useStore.ts"
MODULE = ROOT / "ui/src/stores/musicSong.ts"
CONTROLS = ROOT / "ui/src/components/Sidebar/MusicControls.tsx"
SIDEBAR = ROOT / "ui/src/components/Sidebar/Sidebar.tsx"

SEAM = (
    "    // Lyrics are optional in Music mode; ours, one call: stores/musicSong.ts.\n"
    "    if (!(await ensureMusicSong(get, set))) return\n"
    "    let state = get()"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class MusicLyricsAreOptionalTests(unittest.TestCase):
    def test_the_store_asks_for_the_song_before_it_reads_its_state(self):
        store = _read(STORE)
        self.assertIn("import { ensureMusicSong } from './musicSong'", store)
        # The order is the point: the written lyrics must land in the parameters
        # that this submission builds, not in the snapshot read before them.
        self.assertIn(SEAM, store)

    def test_the_store_stops_when_there_is_nothing_to_submit(self):
        store = _read(STORE)
        self.assertIn("if (!(await ensureMusicSong(get, set))) return", store)

    def test_only_music_mode_is_touched(self):
        module = _read(MODULE)
        self.assertIn("state.generationMode !== 'audio' || state.audioSubMode !== 'music'", module)
        self.assertIn("if (!needsLyrics(state)) return true", module)

    def test_the_description_writes_the_lyrics_with_the_panels_own_writer(self):
        module = _read(MODULE)
        controls = _read(CONTROLS)
        # One writer, not a second engine: the same endpoint the Write Song
        # button already calls.
        self.assertIn("api.writeSong({", module)
        self.assertIn("api.writeSong({", controls)
        self.assertIn("description,", module)
        self.assertIn("instrumental,", module)
        self.assertIn("duration_seconds: state.durationSeconds", module)
        self.assertIn("model_type: String(state.params.model_type || '')", module)

    def test_the_written_words_land_in_the_fields_the_model_reads(self):
        module = _read(MODULE)
        self.assertIn("prompt: lyrics,", module)
        # A Caption the user wrote is never overwritten by the writer's.
        self.assertIn(
            "String(params.alt_prompt || '').trim() || !style ? {} : { alt_prompt: style }",
            module,
        )
        self.assertIn("write(applySong(lyrics, String(written.style || '').trim()))", module)

    def test_an_instrumental_song_uses_the_panels_own_sentinel(self):
        module = _read(MODULE)
        controls = _read(CONTROLS)
        self.assertIn("export const INSTRUMENTAL_LYRICS = '[Instrumental]'", module)
        self.assertIn("setLyrics('[Instrumental]')", controls)
        self.assertIn("instrumental ? INSTRUMENTAL_LYRICS : String(written.lyrics || '').trim()", module)

    def test_an_empty_request_is_refused_with_a_reason(self):
        module = _read(MODULE)
        self.assertIn("announceFailure(", module)
        self.assertIn("'Music needs a song'", module)
        self.assertIn("return false", module)
        self.assertIn("if (!lyrics) {", module)

    def test_the_reason_is_shown_where_the_music_panel_is(self):
        module = _read(MODULE)
        sidebar = _read(SIDEBAR)
        # Music mode does not mount the prompt dock that renders
        # promptEnhanceError, so the reason has to travel as a toast.
        self.assertIn(
            "!(isAudio && ['sfx', 'mixer', 'music'].includes(audioSubMode))",
            sidebar,
        )
        self.assertIn("announceMaestroEvent({", module)
        self.assertIn("category: 'failure',", module)
        self.assertIn("system: false,", module)
        self.assertIn("sound: false,", module)


if __name__ == "__main__":
    unittest.main()
