"""The workflow contract reaches the prompt the planner actually sends.

`test_scene_policy.py` checks the text and the wiring at source level. This file
proves the runtime half: the system prompt handed to the model carries the block,
with the workflow's own rule in it. The 26-clip drama that motivated the policy was
planned by the audio-film prompt, so that is the one measured here in full, plus the
two planners whose prompts are built inside a closure and cannot be read any other
way.

Each planner's LLM call is replaced with one that records the system prompt and then
aborts the planning run, so nothing reaches a model and no fixtures are needed beyond
the clips or transcript the prompt is built from.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.planners.podcast import PodcastPlanner  # noqa: E402
from services.director.planners.short_film import ShortFilmPlanner  # noqa: E402
from services.director.planners.viral_video import ViralVideoPlanner  # noqa: E402


class _Abort(Exception):
    """Stops planning once the prompt has been captured."""


def _capture(planner, **call):
    """Run ``call`` far enough to build the system prompt, then abort."""

    captured: dict[str, str] = {}

    def record(**kwargs):
        captured["system"] = kwargs.get("system_prompt") or ""
        raise _Abort

    planner._call_llm_json = record
    try:
        call["fn"](planner)
    except _Abort:
        pass
    return captured.get("system") or ""


def _audio_film_prompt() -> str:
    planner = ShortFilmPlanner()
    planner._video_model = "minimax_h3_ref2va"
    planner._image_model = "flux"
    planner._seamless = True
    return _capture(
        planner,
        fn=lambda p: p._plan_audio_driven(
            clips=[
                {"start": 0.0, "end": 5.0, "text": "Ese dia los ninos no salieron."},
                {"start": 5.0, "end": 10.0, "text": "Las calles se llenaron de disfraces."},
            ],
            story_description="Una abuela espera a los ninos en la calle del pueblo.",
            lyrics=None,
            speaker_mappings={},
            reference_image_path=None,
            char_profiles=[],
            has_reference=False,
            _bounded_batch=True,
        ),
    )


def _podcast_prompt() -> str:
    return _capture(
        PodcastPlanner(),
        fn=lambda p: p.plan(
            transcript=[
                {"start": 0, "end": 5, "text": "Hola a todos."},
                {"start": 5, "end": 10, "text": "Bienvenidos."},
            ],
            target_duration=10.0,
        ),
    )


def _viral_prompt() -> str:
    return _capture(
        ViralVideoPlanner(),
        fn=lambda p: p.plan(concept="Un perro abre una puerta.", target_duration=15),
    )


class AudioFilmPromptTests(unittest.TestCase):
    def test_the_prompt_carries_the_short_film_contract(self):
        prompt = _audio_film_prompt()

        self.assertTrue(prompt, "the audio-film planner built no system prompt")
        self.assertIn("SCENE AND SHOT PROGRESSION", prompt)
        self.assertIn("SHORT FILM", prompt)

    def test_the_prompt_forbids_holding_one_setting(self):
        prompt = _audio_film_prompt()

        self.assertIn("more than two consecutive shots in the same setting", prompt)
        self.assertIn("Variety must be caused", prompt)

    def test_the_prompt_limits_stillness_and_asks_for_action(self):
        prompt = _audio_film_prompt()

        self.assertIn("Stillness is an accent", prompt)
        self.assertIn("at most one shot in five", prompt)

    def test_the_prompt_keeps_its_own_existing_rules(self):
        # The block is added beside the planner's rules, not in place of them.
        prompt = _audio_film_prompt()

        self.assertIn("The audio is PRE-RECORDED", prompt)
        self.assertIn("Describe the ENVIRONMENT in detail for each shot", prompt)


class OtherWorkflowPromptTests(unittest.TestCase):
    def test_a_podcast_prompt_asks_for_one_studio(self):
        prompt = _podcast_prompt()

        self.assertIn("The studio is the world", prompt)
        self.assertIn("Do not move the hosts", prompt)
        # Its own podcast rules must survive the insertion.
        self.assertIn("Favor steady framing for dialogue", prompt)

    def test_a_viral_prompt_asks_for_the_hook_on_frame_one(self):
        prompt = _viral_prompt()

        self.assertIn("The hook is frame one", prompt)
        self.assertIn("The FIRST SHOT is critical", prompt)

    def test_the_two_workflows_are_not_told_the_same_thing(self):
        podcast = _podcast_prompt()
        viral = _viral_prompt()

        self.assertNotIn("The studio is the world", viral)
        self.assertNotIn("The hook is frame one", podcast)


if __name__ == "__main__":
    unittest.main()
