"""Saving a reviewed prompt must not nest a second prompt inside the first.

The Dashboard shows the *compiled* prompt, so that is what a user edits and what
the save path stored in `_director_h3_source_prompt` -- the field that is meant
to hold the planner's raw shot prompt. Every rerun then compiled a compiled
prompt: the dialogue and the DIALOGUE FORMAT block were duplicated per save. One
real shot reached 17,331 characters with its lines present twice, and MiniMax H3
could no longer work out which referenced character spoke a line:

    MiniMax H3 Omni could not determine which referenced character speaks
    '[Spanish] Pensemos, por ejemplo, en la evolucion de las redes sociales...'

The raw planner prompt never looks compiled -- measured: 0 of 150 raw sources in
a real project contained a Context-IR field header -- so the two forms are told
apart exactly, and a compiled prompt is now used as the prompt instead of as
input to the compiler.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_dialogue import (  # noqa: E402
    compile_h3_clip_plans,
    looks_like_compiled_h3_prompt,
)

RAW_SHOT = (
    "[Shot 6] The focus returns to <Subject 2> (S2), who is framed in a medium "
    "shot. He looks towards his colleague and says, <d>[Spanish] Pensemos en la "
    "evoluci\u00f3n de las redes sociales.</d> His hands move to emphasize the point."
)


def _plan(prompt: str, *, beats=None, subjects=None) -> dict:
    return {
        "video_prompt": prompt,
        "image_prompt": "",
        "_director_h3_source_prompt": prompt,
        "_director_h3_compiled_prompt": "",
        "_director_dialogue_beats": beats or [],
        "_director_subjects_on_screen": subjects or [],
        "_director_h3_prompt_mode": "ref2va",
        "_director_h3_model_family": "ref2va",
        "_director_duration_sec": 8.0,
    }


def _compile(plan: dict) -> str:
    compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])
    return str(plan["video_prompt"])


class CompiledPromptDetectorTests(unittest.TestCase):
    def test_a_compiled_prompt_is_recognised(self):
        text = "subject_definitions: <Subject 1> (S1): a woman.\n\ndetailed_description: [Shot 1] She speaks."

        self.assertTrue(looks_like_compiled_h3_prompt(text))
        self.assertTrue(looks_like_compiled_h3_prompt("retention_analysis: N/A"))

    def test_a_raw_planner_prompt_is_not(self):
        # The whole discriminator rests on this: a planner's shot prompt uses
        # [Shot N] prose and <d> blocks, never the six Context-IR field names.
        self.assertFalse(looks_like_compiled_h3_prompt(RAW_SHOT))
        self.assertFalse(looks_like_compiled_h3_prompt(""))
        self.assertFalse(looks_like_compiled_h3_prompt(None))

    def test_dialogue_alone_does_not_look_compiled(self):
        self.assertFalse(looks_like_compiled_h3_prompt("<d>[Spanish] Hola.</d>"))


class NoNestingTests(unittest.TestCase):
    def test_compiling_a_raw_prompt_still_builds_the_six_fields(self):
        plan = _plan(RAW_SHOT)

        prompt = _compile(plan)

        self.assertIn("subject_definitions:", prompt)
        self.assertIn("detailed_description:", prompt)
        for marker in ("summary", "retention_analysis", "overall_soundscape", "non_diegetic_music"):
            self.assertIn(f"{marker}:", prompt)

    def test_saving_what_the_user_saw_does_not_nest_on_the_next_compile(self):
        # This is the reported failure, reproduced: compile, save the compiled
        # text exactly as the Dashboard would, compile again.
        first = _compile(_plan(RAW_SHOT))

        second = _compile(_plan(first))

        self.assertEqual(second, first)
        self.assertEqual(second.count("detailed_description:"), 1)
        self.assertEqual(second.count("<d>"), first.count("<d>"))

    def test_repeated_saves_stay_stable(self):
        prompt = _compile(_plan(RAW_SHOT))
        size = len(prompt)

        for _ in range(4):
            prompt = _compile(_plan(prompt))
            self.assertEqual(len(prompt), size)

    def test_a_compiled_prompt_is_used_as_the_prompt_not_as_input(self):
        compiled = _compile(_plan(RAW_SHOT))
        plan = _plan(compiled)

        self.assertEqual(_compile(plan), compiled)

    def test_the_edit_flag_is_not_required(self):
        # A clip saved before the flag existed still carries a compiled source,
        # and it must not nest either.
        compiled = _compile(_plan(RAW_SHOT))
        plan = _plan(compiled)
        plan["_director_prompt_user_edited"] = False

        self.assertEqual(_compile(plan), compiled)

    def test_the_compiled_cache_is_left_consistent(self):
        compiled = _compile(_plan(RAW_SHOT))
        plan = _plan(compiled)

        _compile(plan)

        self.assertEqual(plan["_director_h3_compiled_prompt"], compiled)
        self.assertEqual(plan["video_prompt"], compiled)

    def test_a_repaired_clip_is_compiled_normally_again(self):
        # After the repair the source is raw again, so the normal path resumes.
        plan = _plan(RAW_SHOT)
        plan["_director_prompt_user_edited"] = False

        prompt = _compile(plan)

        self.assertTrue(looks_like_compiled_h3_prompt(prompt))
        self.assertFalse(looks_like_compiled_h3_prompt(plan["_director_h3_source_prompt"]))


if __name__ == "__main__":
    unittest.main()
