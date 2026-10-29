"""Shots that read alike even though every prompt is worded differently.

Two measured failures stand behind this report. A 14-clip audio film reused one
entrance in every clip; a 26-clip drama ended with 11 of 26 shots declaring the
frame itself does not move and 13 of 26 having no performed action at all. The
user discovered the second one ten clips into a render, which is the exact cost
this check exists to remove: the prompts must be compared before the GPU work,
not after.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import director_pipeline  # noqa: E402
from services.director_pipeline import _director_prompt_diversity_report  # noqa: E402


def _plan(prompt: str) -> dict:
    return {"video_prompt": prompt}


def _run(plans, pid="", kind=""):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        findings = _director_prompt_diversity_report(plans, pid=pid, kind=kind)
    return findings, buffer.getvalue()


class DistinctPlanTests(unittest.TestCase):
    def test_distinct_action_shots_report_nothing(self):
        plans = [
            _plan("Rosa lifts the knife from the table and turns toward the window."),
            _plan("The children walk past the camera carrying pumpkin lanterns."),
            _plan("The figure raises one hand as the shroud swings open."),
            _plan("Rosa kneels at the altar and presses her palms together."),
        ]

        findings, output = _run(plans)

        self.assertEqual(findings, [])
        self.assertIn("4 distinct shots of 4", output)

    def test_empty_plan_is_not_reported(self):
        findings, output = _run([])

        self.assertEqual(findings, [])
        self.assertEqual(output, "")


class RepeatedPromptTests(unittest.TestCase):
    def test_verbatim_prompt_repeat_is_named(self):
        prompt = "Rosa stands before the wooden table and looks at the knife."
        plans = [_plan(prompt), _plan(prompt), _plan("The bell sways between the trees.")]

        findings, _ = _run(plans)

        self.assertTrue(findings)
        self.assertIn("repeat another clip verbatim", findings[0])
        self.assertIn("clips 1, 2", findings[0])

    def test_similar_but_distinct_prompts_are_accepted(self):
        plans = [
            _plan("Rosa stands before the wooden table and looks at the knife."),
            _plan("Rosa stands before the wooden table and looks at the cup."),
        ]

        findings, _ = _run(plans)

        self.assertFalse([f for f in findings if "verbatim" in f])


class StaticShotTests(unittest.TestCase):
    def test_quarter_of_shots_frozen_is_reported(self):
        static = "Rosa sits at the table. The frame holds on her. No movement visible."
        plans = [_plan(static) for _ in range(2)] + [
            _plan("Rosa rises and walks to the door."),
            _plan("The children run past the church and turn the corner."),
            _plan("The figure turns its head and the bells swing."),
            _plan("Rosa lifts the cup and drinks."),
            _plan("The door opens and blue light spills across the corridor."),
            _plan("Rosa kneels and touches the cold stone floor."),
            _plan("The villagers walk forward and raise their lanterns."),
        ]

        findings, _ = _run(plans)

        frozen = [f for f in findings if "frame itself does not move" in f]
        self.assertEqual(len(frozen), 1)
        self.assertIn("2 of 9 shots", frozen[0])
        # The duplicate prompts are reported separately, not folded into this one.
        self.assertTrue([f for f in findings if "verbatim" in f])

    def test_one_still_shot_in_ten_is_allowed(self):
        static = "Rosa sits at the table. The scene remains perfectly still."
        plans = [_plan(static)] + [
            _plan(f"Rosa turns and crosses the room, passing the window on her left {'x' * index}.")
            for index in range(9)
        ]

        findings, _ = _run(plans)

        self.assertFalse([f for f in findings if "frame itself does not move" in f])

    def test_stillness_below_the_threshold_is_not_reported(self):
        # 2 of 12 is under the 20% line: a film may hold still twice.
        plans = [
            _plan("The room remains still and empty. The fog drifts."),
            _plan("She stays motionless at the threshold. The candle flickers."),
        ] + [_plan(f"Rosa crosses the corridor number {n} and opens the door.") for n in range(10)]

        findings, _ = _run(plans)

        self.assertFalse([f for f in findings if "frame itself does not move" in f])


class MotionlessShotTests(unittest.TestCase):
    def test_shots_with_only_ambient_motion_are_counted(self):
        # The measured 26-clip run: light, fog and bells moving while nobody acts.
        ambient = [
            "The fog thickens around the slate roofs. The streetlamps flicker in unison.",
            "Blue moonlight falls on the broken bell. The shroud sways in the wind.",
            "Candlelight moves across the altar. Incense smoke rises.",
            "The lamp trembles. Mist creeps along the cobblestones.",
        ]
        plans = [_plan(text) for text in ambient] + [
            _plan("Rosa turns from the table and walks into the corridor."),
        ]

        findings, _ = _run(plans)

        noise = [f for f in findings if "no performed action" in f]
        self.assertEqual(len(noise), 1)
        self.assertIn("4 of 5 shots", noise[0])
        self.assertIn("clips 1, 2, 3, 4", noise[0])

    def test_an_action_inside_an_ambient_sentence_counts(self):
        plans = [
            _plan("Fog drifts past the window as Rosa presses the knife into the wood."),
            _plan("The lamp flickers while the children walk toward the camera."),
            _plan("Candles burn behind her as she kneels at the altar."),
            _plan("Smoke rises and the figure lifts one hand toward the glass."),
        ]

        findings, _ = _run(plans)

        self.assertFalse([f for f in findings if "no performed action" in f])


class SameSettingRunTests(unittest.TestCase):
    def _plans(self, settings):
        return [{"video_prompt": f"Someone crosses the frame and turns, shot {n}.",
                 "_director_environment": setting}
                for n, setting in enumerate(settings, start=1)]

    def test_eight_clips_at_one_altar_are_reported_as_one_run(self):
        # The measured drama: every wording differs, the setting does not.
        settings = [
            "A narrow cobblestone street in a stone mountain village.",
            "Edge of a dark forest at night. Twisted trees.",
            "Stone church interior, warm golden light, wooden pews.",
            "Stone church altar, glowing with golden light.",
            "Church altar, flooded with golden light.",
            "Stone church interior, golden light.",
            "Church altar with five new flames.",
            "Church altar, golden light.",
            "Church altar, golden light.",
            "Church altar, golden light fading to warm ochre.",
            "A small damp stone village church at night.",
        ]

        findings, _ = _run(self._plans(settings))

        runs = [f for f in findings if "never leave one setting" in f]
        self.assertEqual(len(runs), 1)
        self.assertIn("clips 3-11", runs[0])
        self.assertIn("9 consecutive shots", runs[0])

    def test_a_move_through_several_rooms_is_not_reported(self):
        settings = [
            "Small domestic chapel, wooden altar.",
            "Narrow stone corridor leading from the chapel to the kitchen.",
            "Rustic stone kitchen with a dark wooden table.",
            "Church altar, golden light.",
            "Forest clearing under blue moonlight.",
            "Kitchen table, cold morning light.",
        ]

        findings, _ = _run(self._plans(settings))

        self.assertFalse([f for f in findings if "never leave one setting" in f])

    def test_two_clips_in_one_place_are_allowed(self):
        settings = [
            "Church altar, golden light.",
            "Church altar, golden light.",
            "Forest clearing under blue moonlight.",
            "Kitchen table, cold morning light.",
        ]

        findings, _ = _run(self._plans(settings))

        self.assertFalse([f for f in findings if "never leave one setting" in f])

    def test_identical_settings_without_a_place_name_are_still_reported(self):
        # "Dark background." names no place, so only the text comparison can see it.
        settings = ["Dark background."] * 3 + ["Church altar, golden light."]

        findings, _ = _run(self._plans(settings))

        self.assertTrue([f for f in findings if "never leave one setting" in f])


class WorkflowAwarenessTests(unittest.TestCase):
    """The same plan means different things in different workflows."""

    def _plans(self):
        # Ten consecutive shots in one church, five of them frozen: the measured
        # 26-clip drama, shortened to the part that matters here.
        rows = []
        for number in range(10):
            still = (
                "Stone church altar, golden light."
                if number < 5 else f"Church altar, golden light, angle {number}."
            )
            prompt = (
                f"S1 narrates shot {number} as the altar remains perfectly still."
                if number < 5
                else f"Rosa turns toward the altar and kneels, shot {number}."
            )
            rows.append({"_director_environment": still, "video_prompt": prompt})
        return rows

    def test_a_short_film_is_reported_for_one_setting_and_stillness(self):
        findings, output = _run(self._plans(), kind="short_film_audio")

        self.assertTrue([f for f in findings if "never leave one setting" in f])
        self.assertTrue([f for f in findings if "frame itself does not move" in f])
        self.assertIn("short film", output)

    def test_a_podcast_is_not_reported_for_staying_in_its_studio(self):
        findings, output = _run(self._plans(), kind="podcast")

        self.assertEqual(findings, [])
        self.assertIn("podcast", output)

    def test_a_podcast_is_still_reported_for_repeating_a_prompt(self):
        plans = self._plans()
        plans[1]["video_prompt"] = plans[0]["video_prompt"]

        findings, _ = _run(plans, kind="podcast")

        self.assertTrue([f for f in findings if "verbatim" in f])

    def test_a_music_video_may_hold_one_stage_longer_than_a_short_film(self):
        # Four consecutive shots in one place: allowed for a music video, which
        # returns to the same stage on purpose, and a stall for a short film.
        rows = [{"_director_environment": "Stage, blue light.",
                 "video_prompt": f"The singer turns and raises the mic, take {n}."}
                for n in range(4)]

        music, _ = _run(rows, kind="music_video")
        film, _ = _run(rows, kind="short_film_story")

        self.assertFalse([f for f in music if "never leave one setting" in f])
        self.assertTrue([f for f in film if "never leave one setting" in f])

    def test_an_unknown_workflow_is_judged_as_a_short_film(self):
        rows = [{"_director_environment": "Church altar, golden light.",
                 "video_prompt": "Someone waits in the dark."} for _ in range(4)]

        findings, _ = _run(rows, kind="interpretive_dance")

        self.assertTrue([f for f in findings if "never leave one setting" in f])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(director_pipeline._pipelines)
        director_pipeline._pipelines.clear()
        director_pipeline._pipelines["variety1"] = {
            "status": "running",
            "pid": "variety1",
        }

    def tearDown(self):
        director_pipeline._pipelines.clear()
        director_pipeline._pipelines.update(self._saved)

    def test_findings_are_published_on_the_pipeline(self):
        static = "The room remains still and empty. No movement visible."
        plans = [_plan(static) for _ in range(4)]

        _run(plans, pid="variety1")

        warnings = director_pipeline._pipelines["variety1"]["plan_warnings"]
        self.assertTrue([w for w in warnings if "frame itself does not move" in w])

    def test_a_clean_plan_clears_previous_warnings(self):
        director_pipeline._pipelines["variety1"]["plan_warnings"] = ["stale finding"]

        _run([_plan("Rosa crosses the room and opens the door.")], pid="variety1")

        self.assertEqual(director_pipeline._pipelines["variety1"]["plan_warnings"], [])


if __name__ == "__main__":
    unittest.main()
