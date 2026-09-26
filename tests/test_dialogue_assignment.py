"""A written script's rows become the dialogue beats, one row to one line.

Measured on the project the user kept re-running (``viral-2``, plan
``_director_pipeline_a020c144.json``): 20 authored rows, a 14-clip timeline that
covered every one of them, and **6 dialogue beats** in the plan with **not one of
them carrying a speaker**. The rows were in the request -- the pipeline passes them
as ``transcript`` -- and the planner ignored them, asking the model to retype the
dialogue instead. A model under cognitive load writes some of it.

The timeline and the transcript below are the real ones, so the crossing case (row 2
spans 3.97-8.97s and its clip ends at 8.71s) is pinned rather than imagined.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.dialogue_assignment import (  # noqa: E402
    assign_rows_to_clips,
    beat_from_row,
    beats_for_shot,
    row_language,
    shot_speakers_note,
)
from services.director.planners.viral_video import ViralVideoPlanner  # noqa: E402
from services.director.schema import DialogueBeat  # noqa: E402


# The real timeline of `viral-2`, in seconds.
_CLIP_WINDOWS = [
    (0.0, 8.7083), (8.7083, 16.0), (16.0, 23.2917), (23.2917, 29.1667),
    (29.1667, 37.1667), (37.1667, 44.4583), (44.4583, 53.875), (53.875, 61.875),
    (61.875, 67.0417), (67.0417, 72.9167), (72.9167, 78.0833), (78.0833, 83.25),
    (83.25, 88.4167), (88.4167, 93.5833),
]
# The real spans of the 20 rows the parser produced from the user's script.
_AUTHORED_ROWS = [
    (0.0, 3.5714), (3.9714, 8.9714), (9.3714, 13.3), (13.7, 17.2714),
    (17.6714, 20.5286), (20.9286, 25.5714), (25.9714, 31.3286), (31.7286, 35.6571),
    (36.0571, 40.3429), (40.7429, 48.2429), (48.6429, 52.2143), (52.6143, 57.9714),
    (58.3714, 61.9429), (62.3429, 66.9857), (67.3857, 72.0286), (72.4286, 78.8571),
    (79.2571, 81.7571), (82.1571, 86.8), (87.2, 88.8), (89.2, 90.8),
]
# Which of the two participants authored each row, and the language it declares.
_SPEAKERS = ["Valeria", "Ricardo"] + ["Valeria", "Ricardo"] * 9
# What the planner actually produced for those 20 rows: six lines, no speaker.
_MEASURED_BEATS = 6


def _clips() -> list[dict]:
    return [{"start": start, "end": end, "label": "scene"} for start, end in _CLIP_WINDOWS]


def _rows(tagged: bool = True) -> list[dict]:
    rows = []
    for index, (start, end) in enumerate(_AUTHORED_ROWS):
        text = f"Line {index + 1} spoken by hand"
        if tagged:
            text = f"<d>[Spanish] {text}</d>"
        rows.append({
            "start": start,
            "end": end,
            "speaker": _SPEAKERS[index],
            "text": text,
        })
    return rows


def _words(authored: list[list[dict]]) -> list[str]:
    return [str(beat.get("spoken_text") or "") for beats in authored for beat in beats]


class RowPlacementTests(unittest.TestCase):
    def test_every_authored_row_becomes_exactly_one_line(self):
        authored = assign_rows_to_clips(_clips(), _rows())
        self.assertEqual(sum(len(beats) for beats in authored), len(_AUTHORED_ROWS))
        self.assertEqual(len(authored), len(_CLIP_WINDOWS))
        # The defect being fixed: 6 beats for 20 rows.
        self.assertGreater(sum(len(beats) for beats in authored), _MEASURED_BEATS)

    def test_no_line_is_lost_and_none_is_spoken_twice(self):
        authored = assign_rows_to_clips(_clips(), _rows())
        words = _words(authored)
        self.assertEqual(len(words), len(set(words)))
        for index in range(len(_AUTHORED_ROWS)):
            self.assertEqual(
                sum(1 for word in words if f"Line {index + 1} " in word), 1,
            )

    def test_a_row_crossing_a_boundary_belongs_to_one_shot_only(self):
        # Row 2 spans 3.97-8.97s and its window ends at 8.71s, so it overlaps two
        # shots. The midpoint decides: overlap alone would have it spoken twice.
        authored = assign_rows_to_clips(_clips(), _rows())
        self.assertEqual([len(beats) for beats in authored][:3], [2, 2, 2])
        self.assertIn("Line 2 ", _words(authored)[1])

    def test_a_row_past_the_last_window_is_not_dropped(self):
        clips = [{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}]
        rows = [{"start": 40.0, "end": 44.0, "speaker": "Valeria", "text": "late line"}]
        authored = assign_rows_to_clips(clips, rows)
        self.assertEqual([len(beats) for beats in authored], [0, 1])
        self.assertEqual(authored[1][0]["spoken_text"], "late line")

    def test_a_timeline_without_timings_assigns_nothing(self):
        # Nothing states a duration, so placement would be arbitrary. The planner's
        # own beats stand instead of every row landing in the first shot.
        clips = [{"label": "verse"} for _ in range(4)]
        authored = assign_rows_to_clips(clips, _rows())
        self.assertEqual([len(beats) for beats in authored], [0, 0, 0, 0])

    def test_an_empty_transcript_leaves_the_plan_alone(self):
        self.assertEqual(
            [len(beats) for beats in assign_rows_to_clips(_clips(), None)],
            [0] * len(_CLIP_WINDOWS),
        )

    def test_the_speaker_and_the_declared_language_survive(self):
        row = {
            "start": 0.0, "end": 3.0, "speaker": "Valeria",
            "text": "<d>[Spanish] La dignidad no se negocia.</d>",
        }
        beat = beat_from_row(row)
        self.assertEqual(beat["speaker_id"], "Valeria")
        self.assertEqual(beat["language"], "Spanish")
        # The wrapper is removed at the boundary every planner beat crosses, and the
        # structured language survives it: the compiler needs the language, not the tag.
        normalized = DialogueBeat.from_dict(beat)
        self.assertEqual(normalized.spoken_text, "La dignidad no se negocia.")
        self.assertEqual(normalized.speaker_id, "Valeria")
        self.assertEqual(normalized.language, "Spanish")

    def test_an_untagged_row_invents_no_language(self):
        row = {"start": 0.0, "end": 3.0, "speaker": "Valeria", "text": "Sin etiqueta."}
        self.assertEqual(row_language(row), "")
        self.assertNotIn("language", beat_from_row(row))
        self.assertIsNone(DialogueBeat.from_dict(beat_from_row(row)).language)

    def test_blank_and_no_speech_rows_are_not_lines(self):
        clips = [{"start": 0.0, "end": 10.0}]
        rows = [
            {"start": 0.0, "end": 1.0, "speaker": "Valeria", "text": "   "},
            {"start": 1.0, "end": 2.0, "speaker": "Valeria", "text": "<d>[silent]</d>"},
            {"start": 2.0, "end": 3.0, "speaker": "Valeria", "text": "solo esta linea"},
        ]
        authored = assign_rows_to_clips(clips, rows)
        self.assertEqual(len(authored[0]), 1)
        self.assertEqual(authored[0][0]["spoken_text"], "solo esta linea")

    def test_the_planner_keeps_its_own_beats_for_a_shot_with_no_line(self):
        authored = [[], [{"spoken_text": "authored"}]]
        own = {"dialogue_beats": [{"spoken_text": "the model's line"}]}
        self.assertEqual(beats_for_shot(authored, 0, own)[0]["spoken_text"], "the model's line")
        self.assertEqual(beats_for_shot(authored, 1, own)[0]["spoken_text"], "authored")
        # Past the end of the timeline: the model's beats, never an IndexError.
        self.assertEqual(beats_for_shot(authored, 9, own)[0]["spoken_text"], "the model's line")


class SpeakerHintTests(unittest.TestCase):
    def test_the_hint_names_the_people_who_speak_in_each_shot(self):
        authored = assign_rows_to_clips(_clips(), _rows())
        hint = shot_speakers_note(authored, 0, 3)
        self.assertIn("SPEAKERS IN THESE SHOTS", hint)
        self.assertIn("Shot 1: Valeria, Ricardo", hint)
        self.assertIn("Shot 3: Valeria, Ricardo", hint)
        self.assertNotIn("Shot 4:", hint)

    def test_a_machine_id_is_not_a_speaker_name(self):
        # An analysed project's rows use raw ids; telling the model "SPEAKER_00" is
        # worse than telling it nothing, so those shots are left out.
        authored = [[{"spoken_text": "hi", "speaker_id": "SPEAKER_00"}]]
        authored.append([{"spoken_text": "ho", "speaker_id": "char_1"}])
        self.assertEqual(shot_speakers_note(authored, 0, 2), "")


class _QuietModel:
    """Stands in for the model, which wrote 6 lines for 20 rows in the real run."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, **kwargs):
        system = str(kwargs.get("system_prompt") or "")
        self.prompts.append(system)
        match = re.search(r"Output exactly (\d+) shot plans", system)
        count = int(match.group(1)) if match else 1
        return json.dumps([
            {
                "scene_goal": f"Scene {index + 1}",
                "scene_type": "escalation",
                "duration_sec": 7,
                "subjects_on_screen": [],
                "action_beats": ["they talk"],
                "camera_plan": {"framing": "medium shot"},
                "audio_plan": {"mode": "generated_audio"},
                "ending_beat": "",
            }
            for index in range(count)
        ])


class PlannerCarriesTheAuthoredLinesTests(unittest.TestCase):
    """The reported bug, end to end through the planner."""

    def _plan(self, recorder: _QuietModel):
        planner = ViralVideoPlanner(llm_generate=recorder)
        return planner.plan(
            concept="Valeria y Ricardo hablan de filosofia.",
            target_duration=30,
            platform="general",
            style="cinematic",
            clips=_clips(),
            transcript=_rows(),
        )

    def test_the_planner_places_all_twenty_rows(self):
        recorder = _QuietModel()
        plan = self._plan(recorder)
        beats = [
            beat for shot in plan.shots for beat in (shot.dialogue_beats or [])
        ]
        self.assertEqual(len(beats), len(_AUTHORED_ROWS))
        self.assertGreater(len(beats), _MEASURED_BEATS)

    def test_every_beat_carries_its_speaker(self):
        plan = self._plan(_QuietModel())
        beats = [beat for shot in plan.shots for beat in (shot.dialogue_beats or [])]
        self.assertTrue(all(beat.speaker_id for beat in beats))
        self.assertEqual({beat.speaker_id for beat in beats}, {"Valeria", "Ricardo"})

    def test_the_words_are_the_script_s_own(self):
        # The model contributed no dialogue at all, so every line in the plan came
        # from the authored rows.
        plan = self._plan(_QuietModel())
        texts = {
            beat.spoken_text
            for shot in plan.shots
            for beat in (shot.dialogue_beats or [])
        }
        for index in range(len(_AUTHORED_ROWS)):
            self.assertIn(f"Line {index + 1} spoken by hand", texts)

    def test_the_prompt_names_the_speakers_of_the_batch(self):
        recorder = _QuietModel()
        self._plan(recorder)
        self.assertIn("SPEAKERS IN THESE SHOTS", recorder.prompts[0])
        self.assertIn("Valeria", recorder.prompts[0])


if __name__ == "__main__":
    unittest.main()
