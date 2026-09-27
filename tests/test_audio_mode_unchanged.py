"""The audio-driven mode must not move while the written-script mode is fixed.

Every fix in this series was measured on a script project -- no uploaded track, so the
model has to generate the speech. A project that supplies its own audio is the older,
working path, and it shares the code that changed: the row placement, the shot length
and the project context. These tests pin what that path is entitled to keep, so a
future fix that reaches into it fails here instead of being discovered by ear.

Deliberately NOT pinned here: what a no-audio plan does with the project context. That
prose is read aloud when the model generates the speech (measured twice), and leaving
it out is an open, intended change. Pinning the current behaviour would block it.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.dialogue_assignment import (  # noqa: E402
    assign_rows_to_clips,
    beats_for_shot,
)
from services.director.timeline_planning import shot_duration  # noqa: E402


# The shape an analysed project's transcript really has: raw pyannote ids.
ANALYSED_ROWS = [
    {"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00", "text": "Buenas noches a todos."},
    {"start": 3.4, "end": 7.0, "speaker": "SPEAKER_01", "text": "Y gracias por venir."},
]
ANALYSED_CLIPS = [
    {"start": 0.0, "end": 5.0, "label": "intro"},
    {"start": 5.0, "end": 10.0, "label": "verse"},
]


class AnalysedTranscriptTests(unittest.TestCase):
    def test_raw_speaker_ids_are_left_for_the_audio_binding(self):
        authored = assign_rows_to_clips(ANALYSED_CLIPS, ANALYSED_ROWS)

        speakers = [beat["speaker_id"] for beats in authored for beat in beats]
        self.assertEqual(speakers, ["SPEAKER_00", "SPEAKER_01"])
        # No name-to-label mapping, and no name kept: the audio knows better.
        for beats in authored:
            for beat in beats:
                self.assertNotIn("speaker_name", beat)

    def test_the_audio_path_keeps_the_tone_the_model_wrote(self):
        # With a supplied track the planner still writes the line direction, and the
        # model never invents speech there, so the tone is what carries the delivery.
        authored = assign_rows_to_clips(ANALYSED_CLIPS, ANALYSED_ROWS)

        beats = beats_for_shot(
            authored, 0, {"dialogue_beats": [{"spoken_text": "otra cosa", "delivery": "sereno"}]},
        )
        self.assertTrue(beats[0]["delivery"].endswith("sereno"))

    def test_the_words_come_from_the_transcript_not_from_the_model(self):
        authored = assign_rows_to_clips(ANALYSED_CLIPS, ANALYSED_ROWS)

        beats = beats_for_shot(
            authored, 0, {"dialogue_beats": [{"spoken_text": "inventado"}]},
        )
        self.assertEqual(beats[0]["spoken_text"], "Buenas noches a todos.")


class ShotLengthTests(unittest.TestCase):
    def test_an_audio_project_takes_its_clip_window_as_the_shot_length(self):
        # The renderer builds the frame count from the window, so the prompt must state
        # the same length; a model-chosen duration disagreed with the clip here too.
        raw = {"duration_sec": 4}
        self.assertAlmostEqual(shot_duration(ANALYSED_CLIPS, 1, raw), 5.0)
        # With no window to take, the model still chooses.
        self.assertEqual(shot_duration([{"label": "verse"}], 0, raw), 4.0)


class SuppliedTrackKeepsItsContextTests(unittest.TestCase):
    """A supplied track never invents speech, so its context stays in the prompt."""

    CONTEXT = (
        "RESTRICCIONES GLOBALES DEL PROYECTO (critico, no negociable): "
        "- Este proyecto tiene EXACTAMENTE DOS participantes."
    )

    def _body(self, audio_plan):
        from services.director.h3_dialogue import _source_prompt_parts

        body, _soundscape, _music, _blocks = _source_prompt_parts(
            "[Shot 1] Valeria speaks.",
            project_context=self.CONTEXT,
            audio_plan=audio_plan,
        )
        return body

    def test_a_driving_audio_plan_still_carries_the_project_context(self):
        body = self._body({"mode": "audio_driven", "timing_anchor": "audio"})
        self.assertIn("Project context (the whole film", body)
        self.assertIn("no negociable", body)

    def test_a_music_driven_plan_still_carries_the_project_context(self):
        body = self._body({"mode": "music_driven", "timing_anchor": "audio"})
        self.assertIn("Project context (the whole film", body)


if __name__ == "__main__":
    unittest.main()
