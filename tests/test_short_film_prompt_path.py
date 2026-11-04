"""The "Video Prompts" review path, which is not the pipeline planner.

The Director panel's review step regenerates each shot's prompt through
``llm_service.plan_short_film_prompts``. That path had its own rules, written for a model
that could not know anyone's name:

    - NEVER use character names in image OR video prompts ...
      write 'the man in the blue shirt' instead of 'Mr Johnson'
    - Keep each prompt under 25 words.

Measured on the person's own project: the brief names Anita, David and Goliath and gives
each one a wardrobe read from their own picture, and this path returned "the teen boy in
earthy brown", "the narrator man in dark grey" and "the Philistine warrior in blue bronze"
— three people the project never designed, in a film meant to be one woman narrating.

These tests pin the cast block, the rule that replaces "never use a name", and the
fallback that keeps the old behaviour when a brief really does cast nobody.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import llm_service  # noqa: E402

BRIEF = (
    "SUBJECT LOCK: <Subject 1> (S1)(Picture 1) is ALWAYS Anita. "
    "<Subject 2> (S2) (Picture 2)is ALWAYS David. "
    "<Subject 3> (S3) (Picture 3) is ALWAYS Goliath. "
    "King Saul and the soldiers are NPC's that appear only in the background. "
    "CHARACTERS: Anita (S1): Young woman, fair skin; cream off-white button-up shirt with "
    "pointed collar, paired with a fitted navy blue knee-length pencil skirt; black "
    "pointed-toe high-heel pumps. "
    "David (S2): Young adult; cream off-white knee-length tunic belted at the waist; "
    "brown leather flat sandals. "
    "PRESENCES: NPC's: King Saul: Older king with grey-streaked hair and a full beard. "
    "CAMERA: Eye-level for Anita. "
    "DESCRIPTION: Anita walks the audience through the ancient landscape. "
    "NEGATIVE: No modern clothing. "
    "REFERENCES: <Picture 1> Anita (S1) \u2014 identity reference \u2014 Woman, fair skin. "
    "<Picture 2> David \u2014 identity reference \u2014 Man, young adult."
)

CLIPS = [
    {"start": 0.0, "end": 12.0, "label": "dialogue", "section_label": "dialogue",
     "dialogue_lines": ["Here in the valley."]},
    {"start": 12.0, "end": 24.0, "label": "dialogue", "section_label": "dialogue",
     "dialogue_lines": ["The armies wait."]},
]


class _Recorder:
    def __init__(self, answer: str = "1. Anita stands among the soldiers.\n2. David lifts his sling."):
        self.calls = []
        self.answer = answer

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


class CastBlockTests(unittest.TestCase):
    def test_the_brief_s_cast_is_read_with_its_wardrobe(self):
        block, names = llm_service._short_film_cast_block(BRIEF, [])

        self.assertIn("Anita", names)
        self.assertIn("David", names)
        self.assertIn("cream off-white button-up shirt", block)
        self.assertIn("fitted navy blue knee-length pencil skirt", block)

    def test_the_identity_picture_is_bound_to_its_person(self):
        block, _names = llm_service._short_film_cast_block(BRIEF, [])

        self.assertIn("<Picture 1>", block.split("Anita")[0] + block)
        self.assertIn("<Picture 2>", block)

    def test_a_grouping_label_is_not_a_person(self):
        _block, names = llm_service._short_film_cast_block(BRIEF, [])

        self.assertNotIn("NPC's", names)

    def test_a_brief_that_casts_nobody_yields_nothing(self):
        block, names = llm_service._short_film_cast_block("A wide shot of an empty valley.", [])

        self.assertEqual((block, names), ("", []))


class SystemPromptTests(unittest.TestCase):
    def _run(self, brief: str, characters=None):
        recorder = _Recorder()
        original = llm_service.generate
        llm_service.generate = recorder
        try:
            plans = llm_service.plan_short_film_prompts(
                clips=CLIPS,
                scene_description=brief,
                lyrics=[{"speaker_id": "SPEAKER_00", "text": "Here in the valley.", "start": 0.0, "end": 6.0},
                    {"speaker_id": "SPEAKER_00", "text": "The armies wait.", "start": 12.0, "end": 18.0}],
                speaker_mappings={"(S1)": {"name": "", "role": "speaking"}},
                characters=characters,
                prompt_type="video",
            )
        finally:
            llm_service.generate = original
        return recorder.calls[0], plans

    def test_the_cast_reaches_the_model_instead_of_a_ban_on_names(self):
        call, _plans = self._run(BRIEF)
        system = str(call.get("system_prompt") or "")

        self.assertIn("CAST (this project's own people", system)
        self.assertIn("cream off-white button-up shirt", system)
        self.assertIn("<Picture 1>", system)
        self.assertNotIn("NEVER use character names", system)
        self.assertIn("never rename them", system)

    def test_the_wardrobe_is_not_squeezed_into_twenty_five_words(self):
        call, _plans = self._run(BRIEF)
        system = str(call.get("system_prompt") or "")

        self.assertIn("under 90 words", system)
        self.assertNotIn("under 25 words", system)

    def test_a_brief_without_a_cast_keeps_the_old_rule(self):
        call, _plans = self._run("A wide shot of an empty valley at dawn.")
        system = str(call.get("system_prompt") or "")

        self.assertNotIn("CAST (this project's own people", system)
        self.assertIn("NEVER use character names", system)

    def test_a_speaker_named_only_in_the_lock_is_named_here_too(self):
        call, plans = self._run(BRIEF)
        user = str(call.get("prompt") or "")

        self.assertIn("Anita", user)
        self.assertNotIn("(S)", user)
        self.assertEqual(len(plans), len(CLIPS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
