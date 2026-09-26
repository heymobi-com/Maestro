"""A subject row the model under-wrote must not abort the render.

Measured: a real viral project died at planning with

    File "app/services/director/schema.py", line 157, in from_dict
        visual_description=d["visual_description"],
    KeyError: 'visual_description'

from `viral_video.py`'s subject list, which is the one place in the whole Director
schema that indexed a key straight out of model output. Every other boundary says so
out loud -- see `DialogueBeat.from_dict`, "LLM-produced structured output is still
untrusted at this boundary" -- and this one aborted a finished plan over a field the
model simply left out. `speaker_name` is a legal optional key of the same row, so a
row that names who speaks and forgets to describe them is exactly what was seen.

The blast radius is every planner: six call sites plus the plan round-trip in
`ShotPlan.from_dict` all go through this one function.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.dialogue_assignment import shot_speakers_note  # noqa: E402
from services.director.planners.viral_video import ViralVideoPlanner  # noqa: E402
from services.director.schema import DialogueBeat, ShotPlan, SubjectRef  # noqa: E402


class SubjectRowBoundaryTests(unittest.TestCase):
    def test_a_row_that_only_names_its_speaker_is_not_a_crash(self):
        subject = SubjectRef.from_dict({"speaker_name": "Valeria"})
        self.assertEqual(subject.visual_description, "Valeria")
        self.assertEqual(subject.speaker_name, "Valeria")

    def test_a_row_that_only_carries_a_character_id_is_not_a_crash(self):
        self.assertEqual(
            SubjectRef.from_dict({"character_id": "char_1"}).visual_description,
            "char_1",
        )

    def test_a_row_with_nothing_usable_still_describes_a_person(self):
        self.assertEqual(SubjectRef.from_dict({}).visual_description, "a person")
        # A key that is present but blank is the same as a missing one.
        self.assertEqual(
            SubjectRef.from_dict({"visual_description": "  "}).visual_description,
            "a person",
        )

    def test_a_bare_string_row_is_accepted(self):
        # `ShotPlan.from_dict` builds rows without the isinstance guard the planners
        # have, so a plain string in the list reached the same KeyError.
        self.assertEqual(
            SubjectRef.from_dict("a person in a hat").visual_description,
            "a person in a hat",
        )

    def test_the_description_the_model_did_write_still_wins(self):
        subject = SubjectRef.from_dict({
            "visual_description": "a woman in a cream dress",
            "speaker_name": "Valeria",
            "wardrobe": "cream A-line dress",
        })
        self.assertEqual(subject.visual_description, "a woman in a cream dress")
        self.assertEqual(subject.speaker_name, "Valeria")
        self.assertEqual(subject.wardrobe, "cream A-line dress")

    def test_a_shot_plan_round_trips_a_name_only_row(self):
        shot = ShotPlan.from_dict({
            "shot_id": "vv_0",
            "index": 0,
            "duration_sec": 5,
            "subjects_on_screen": [{"speaker_name": "Valeria"}],
        })
        self.assertEqual(len(shot.subjects_on_screen), 1)
        self.assertEqual(shot.subjects_on_screen[0].visual_description, "Valeria")


class BeatNormalizationStillHappensTests(unittest.TestCase):
    """The normalisation moved out of `schema.py`; the behaviour did not move."""

    def test_the_h3_wrapper_and_the_repeated_label_are_removed(self):
        beat = DialogueBeat.from_dict({"spoken_text": "<d>(S1): Qué loco.</d>"})
        self.assertEqual(beat.spoken_text, "Qué loco.")

    def test_a_clean_line_is_untouched_and_a_silent_one_is_empty(self):
        self.assertEqual(
            DialogueBeat.from_dict({"spoken_text": "Una línea limpia."}).spoken_text,
            "Una línea limpia.",
        )
        self.assertEqual(DialogueBeat.from_dict({"spoken_text": "[silent]"}).spoken_text, "")
        self.assertEqual(DialogueBeat.from_dict({}).spoken_text, "")


def _timeline(count: int, seconds_each: float = 8.0) -> list[dict]:
    return [
        {"start": index * seconds_each, "end": (index + 1) * seconds_each}
        for index in range(count)
    ]


class _NameOnlySubjectModel:
    """Answers exactly what aborted the render: a row that is only a name."""

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
                "duration_sec": 8,
                "subjects_on_screen": [{"speaker_name": "Valeria"}],
                "action_beats": ["she speaks"],
                "camera_plan": {"framing": "medium shot"},
                "audio_plan": {"mode": "generated_audio"},
                "ending_beat": "",
            }
            for index in range(count)
        ])


class ThePlannerSurvivesItTests(unittest.TestCase):
    def test_planning_does_not_abort_on_a_name_only_row(self):
        recorder = _NameOnlySubjectModel()
        planner = ViralVideoPlanner(llm_generate=recorder)
        plan = planner.plan(
            concept="Valeria y Ricardo hablan de filosofia.",
            target_duration=30,
            platform="general",
            style="cinematic",
            clips=_timeline(4),
        )
        self.assertEqual(len(plan.shots), 4)
        for shot in plan.shots:
            self.assertEqual(len(shot.subjects_on_screen), 1)
            self.assertEqual(shot.subjects_on_screen[0].visual_description, "Valeria")

    def test_the_prompt_does_not_invite_a_name_only_row(self):
        # The hint that names each shot's speakers is what made the model write one.
        hint = shot_speakers_note([[{"spoken_text": "hi", "speaker_id": "Valeria"}]], 0, 1)
        self.assertIn("still carries its full visual_description", hint)
        self.assertIn("never write a row that is only a name", hint)


if __name__ == "__main__":
    unittest.main()
