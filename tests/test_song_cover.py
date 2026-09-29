"""Reading a recorded song for a cover: the labels, the seam, and the reuse.

A cover needs the words of the original, and the app already owns every piece
that produces them: the RoFormer vocal separator, Whisper medium on disk, the
analysis' sections and the repetition-based section namer. What is new here is
only the glue, so these tests pin the glue and then pin the reuse — a future
change that quietly starts a second engine, or drops the one line that registers
the route, has to fail here instead of failing in a cover run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services.song_cover import (  # noqa: E402
    INSTRUMENTAL_LYRICS,
    SONG_LYRICS_ROUTE,
    _display_label,
    labelled_lyrics,
    section_index,
)

MODULE = ROOT / "app/services/song_cover.py"
LAUNCH = ROOT / "app/launch.py"

_SECTIONS = [
    {"start": 0.0, "end": 10.0, "label": "intro"},
    {"start": 10.0, "end": 30.0, "label": "verse"},
    {"start": 30.0, "end": 60.0, "label": "chorus"},
    {"start": 60.0, "end": 75.0, "label": "verse"},
]


class SectionIndexTests(unittest.TestCase):
    def test_the_lines_midpoint_decides_its_section(self):
        # A line that starts in the intro and ends in the first verse is sung
        # in one of them, and the midpoint is the rule the Director uses too.
        self.assertEqual(section_index({"start": 8.0, "end": 12.0}, _SECTIONS), 1)

    def test_a_line_outside_every_section_has_no_section(self):
        self.assertIsNone(section_index({"start": 200.0, "end": 201.0}, _SECTIONS))

    def test_malformed_rows_never_raise(self):
        self.assertIsNone(section_index({}, _SECTIONS))
        self.assertIsNone(section_index({"start": "x", "end": "y"}, _SECTIONS))
        self.assertIsNone(section_index({"start": 1.0, "end": 2.0}, [{"label": "verse"}]))


class LabelledLyricsTests(unittest.TestCase):
    def test_labels_follow_the_order_of_the_song(self):
        lyrics = labelled_lyrics(
            [
                {"start": 1.0, "end": 4.0, "text": "primera linea"},
                {"start": 12.0, "end": 16.0, "text": "segunda linea"},
                {"start": 32.0, "end": 36.0, "text": "el coro"},
            ],
            _SECTIONS,
        )
        self.assertEqual(
            lyrics,
            "[Intro]\nprimera linea\n\n[Verse]\nsegunda linea\n\n[Chorus]\nel coro",
        )

    def test_two_sections_with_the_same_label_stay_two_blocks(self):
        lyrics = labelled_lyrics(
            [
                {"start": 12.0, "end": 16.0, "text": "verso uno"},
                {"start": 62.0, "end": 66.0, "text": "verso dos"},
            ],
            _SECTIONS,
        )
        self.assertEqual(lyrics, "[Verse]\nverso uno\n\n[Verse]\nverso dos")

    def test_a_wordless_section_is_not_declared_instrumental(self):
        # A missed line is far more likely than a wordless verse, and claiming
        # otherwise would delete words the singer actually sings.
        lyrics = labelled_lyrics(
            [{"start": 32.0, "end": 36.0, "text": "solo el coro"}],
            _SECTIONS,
        )
        self.assertEqual(lyrics, "[Chorus]\nsolo el coro")
        self.assertNotIn(INSTRUMENTAL_LYRICS, lyrics)

    def test_lines_outside_the_sections_still_appear(self):
        lyrics = labelled_lyrics(
            [{"start": 500.0, "end": 504.0, "text": "sin seccion"}],
            _SECTIONS,
        )
        self.assertEqual(lyrics, "[Verse]\nsin seccion")

    def test_empty_segments_produce_nothing(self):
        self.assertEqual(labelled_lyrics([], _SECTIONS), "")
        self.assertEqual(labelled_lyrics([{"start": 1.0, "end": 2.0, "text": "   "}], _SECTIONS), "")

    def test_a_missing_label_falls_back_to_verse(self):
        self.assertEqual(_display_label(""), "Verse")
        self.assertEqual(_display_label(None), "Verse")
        self.assertEqual(_display_label("CHORUS"), "Chorus")


class ReuseTests(unittest.TestCase):
    """The pieces this feature is built from already existed."""

    def setUp(self):
        self.module = MODULE.read_text(encoding="utf-8")

    def test_the_voice_is_separated_with_the_app_own_separator(self):
        self.assertIn("from preprocessing.extract_vocals import get_vocals", self.module)
        self.assertIn("return get_vocals(audio_path, stem)", self.module)

    def test_transcription_uses_the_local_medium_model(self):
        # Already on disk (ckpts/whisper_medium): no download, and medium for
        # singing instead of the analysis' faster-whisper "small".
        self.assertIn("from shared.deepy.transcription import _load_whisper_medium", self.module)
        self.assertNotIn("WhisperModel(", self.module)

    def test_a_repeated_chorus_cannot_become_a_loop_of_invented_lines(self):
        self.assertIn("condition_on_previous_text=False", self.module)

    def test_sections_come_from_the_existing_analysis(self):
        self.assertIn("audio_analysis.analyze(audio_path, transcribe=False)", self.module)
        self.assertIn("llm_service.classify_song_sections(", self.module)

    def test_the_route_is_registered_from_the_app(self):
        launch = LAUNCH.read_text(encoding="utf-8")
        self.assertIn("from services.song_cover import register_routes as _register_song_cover", launch)
        # The app's own LLM loader travels with the registration: the module
        # never imports the application, but section naming still gets a model.
        self.assertIn("_register_song_cover(api, ensure_llm=_ensure_llm_loaded)", launch)
        self.assertEqual(SONG_LYRICS_ROUTE, "/api/v1/music/cover-lyrics")
        self.assertIn(f'@api.post(SONG_LYRICS_ROUTE)', self.module)

    def test_registering_the_routes_really_adds_them(self):
        # Not the source text: an app that answers on the documented path.
        try:
            from fastapi import FastAPI
        except ImportError:  # pragma: no cover - the app env always has FastAPI
            self.skipTest("FastAPI is not installed in this interpreter")
        from services.song_cover import register_routes

        app = FastAPI()
        register_routes(app)

        self.assertIn(SONG_LYRICS_ROUTE, {route.path for route in app.routes})

    def test_the_route_answers_a_real_request(self):
        """A handler FastAPI cannot inject into answers 422 for everything.

        That is exactly how "Reading the recording failed" reached the user: the
        handler's parameter was not annotated as a Request, so FastAPI read it
        as a required query field and refused every call before the body was
        ever parsed. Checking the source text would not have caught it; calling
        the route does.
        """

        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - both are in the app env
            self.skipTest("FastAPI/httpx are not installed in this interpreter")
        from services.song_cover import register_routes

        app = FastAPI()
        register_routes(app)
        client = TestClient(app)

        # Our own validation, not FastAPI's: the handler ran and read the body.
        missing_audio = client.post(SONG_LYRICS_ROUTE, json={})
        self.assertEqual(missing_audio.status_code, 400)
        self.assertIn("audio_path", missing_audio.json()["detail"])

        # And a file that is not there is refused by name, without loading a model.
        absent = client.post(SONG_LYRICS_ROUTE, json={"audio_path": "no/such/recording.wav"})
        self.assertEqual(absent.status_code, 404)
        self.assertIn("not found", absent.json()["detail"].lower())

    def test_an_instrumental_answer_never_invents_words(self):
        self.assertEqual(INSTRUMENTAL_LYRICS, "[Instrumental]")
        self.assertIn('"lyrics": INSTRUMENTAL_LYRICS,', self.module)


if __name__ == "__main__":
    unittest.main()
