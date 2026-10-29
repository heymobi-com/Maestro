"""Each workflow is a different craft, and the planner is told so.

A podcast lives in one studio and is carried by framing and reaction; a short film
that stays in one room for ten clips is a mistake; a music video returns to the same
stage on purpose; a viral clip has to hook on frame one. Until this policy existed,
the four planners said nothing about that difference, so the planner applied whatever
the brief happened to emphasise — which is how a 26-clip drama ended with ten
consecutive shots inside one church and eleven prompts declaring the frame motionless.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.scene_policy import (  # noqa: E402
    DEFAULT_KIND,
    MUSIC_VIDEO,
    PODCAST,
    SHORT_FILM_AUDIO,
    SHORT_FILM_STORY,
    VIRAL_VIDEO,
    scene_guidance,
    scene_policy,
)

PLANNERS = APP / "services/director/planners"


class GuidanceTextTests(unittest.TestCase):
    def test_a_short_film_is_asked_to_keep_moving(self):
        text = scene_guidance(SHORT_FILM_AUDIO)

        self.assertIn("SHORT FILM", text)
        self.assertIn("more than two consecutive shots in the same setting", text)
        self.assertIn("Stillness is an accent, never the default", text)
        self.assertIn("one shot in five", text)

    def test_a_podcast_is_asked_to_stay_in_its_studio(self):
        text = scene_guidance(PODCAST)

        self.assertIn("PODCAST", text)
        self.assertIn("The studio is the world", text)
        self.assertIn("Do not move the hosts", text)

    def test_a_music_video_is_allowed_to_return_to_one_stage(self):
        text = scene_guidance(MUSIC_VIDEO)

        self.assertIn("MUSIC VIDEO", text)
        self.assertIn("may return to one set", text)
        self.assertIn("intended repetition", text)

    def test_a_viral_video_is_asked_to_hook_on_frame_one(self):
        text = scene_guidance(VIRAL_VIDEO)

        self.assertIn("VIRAL VIDEO", text)
        self.assertIn("frame one", text)

    def test_the_two_short_film_paths_share_one_contract(self):
        self.assertEqual(
            scene_guidance(SHORT_FILM_AUDIO), scene_guidance(SHORT_FILM_STORY),
        )

    def test_no_two_workflows_are_told_the_same_thing(self):
        texts = {
            kind: scene_guidance(kind)
            for kind in (SHORT_FILM_AUDIO, MUSIC_VIDEO, PODCAST, VIRAL_VIDEO)
        }

        self.assertEqual(len(set(texts.values())), len(texts))

    def test_the_short_film_contract_does_not_forbid_causal_variety(self):
        # The story-driven prompt already says "never create an unrelated scene
        # merely for visual variety". The policy must agree with it instead of
        # fighting it, or the writer receives two opposite orders.
        text = scene_guidance(SHORT_FILM_STORY)

        self.assertIn("Variety must be caused, never decorative", text)
        self.assertIn("needs no excuse", text)


class LimitTests(unittest.TestCase):
    def test_a_podcast_is_never_reported_for_holding_one_setting(self):
        policy = scene_policy(PODCAST)

        self.assertEqual(policy.max_consecutive_same_setting, 0)
        self.assertEqual(policy.stillness_limit, 1.0)
        self.assertFalse(policy.requires_performed_action)

    def test_a_short_film_may_hold_a_setting_for_two_shots(self):
        policy = scene_policy(SHORT_FILM_AUDIO)

        self.assertEqual(policy.max_consecutive_same_setting, 2)
        self.assertAlmostEqual(policy.stillness_limit, 0.2)
        self.assertTrue(policy.requires_performed_action)

    def test_a_music_video_tolerates_more_of_one_place_and_more_stillness(self):
        music = scene_policy(MUSIC_VIDEO)
        short = scene_policy(SHORT_FILM_AUDIO)

        self.assertGreater(
            music.max_consecutive_same_setting, short.max_consecutive_same_setting,
        )
        self.assertGreater(music.stillness_limit, short.stillness_limit)

    def test_a_viral_video_is_the_strictest_about_stillness(self):
        viral = scene_policy(VIRAL_VIDEO)

        for other in (SHORT_FILM_AUDIO, SHORT_FILM_STORY, MUSIC_VIDEO, PODCAST):
            self.assertLessEqual(viral.stillness_limit, scene_policy(other).stillness_limit)

    def test_labels_name_the_workflow(self):
        self.assertIn("short film", scene_policy(SHORT_FILM_AUDIO).label)
        self.assertEqual(scene_policy(PODCAST).label, "podcast")


class ResolutionTests(unittest.TestCase):
    def test_aliases_resolve_to_their_workflow(self):
        self.assertEqual(scene_policy("short_film").kind, SHORT_FILM_AUDIO)
        self.assertEqual(scene_policy("story").kind, SHORT_FILM_STORY)
        self.assertEqual(scene_policy("music").kind, MUSIC_VIDEO)
        self.assertEqual(scene_policy("viral").kind, VIRAL_VIDEO)

    def test_an_unknown_or_missing_workflow_falls_back_to_a_short_film(self):
        for value in ("", None, "interpretive_dance"):
            self.assertEqual(scene_policy(value).kind, DEFAULT_KIND)

    def test_case_and_whitespace_do_not_matter(self):
        self.assertEqual(scene_policy("  PODCAST \n").kind, PODCAST)


class PlannerWiringTests(unittest.TestCase):
    """Each planner has to interpolate the block, not merely import it."""

    def _source(self, name: str) -> str:
        return (PLANNERS / f"{name}.py").read_text(encoding="utf-8")

    def test_every_planner_asks_for_its_own_contract(self):
        expected = {
            "short_film": ['scene_guidance("short_film_audio")', 'scene_guidance("short_film_story")'],
            "podcast": ['scene_guidance("podcast")'],
            "viral_video": ['scene_guidance("viral_video")'],
            "music_video": ['scene_guidance("music_video")'],
        }
        for planner, needles in expected.items():
            source = self._source(planner)
            for needle in needles:
                self.assertIn(needle, source, f"{planner}.py never asks for {needle}")

    def test_the_block_is_interpolated_into_an_f_string(self):
        # A reference inside a plain string would reach nobody: the planner has to
        # put the text into the prompt that is sent, not just compute it.
        source = self._source("podcast")
        self.assertIn("{scene_guidance(\"podcast\")}", source)

    def test_the_short_film_audio_prompt_uses_the_computed_block(self):
        source = self._source("short_film")
        self.assertIn("{scene_guidance_block}", source)


if __name__ == "__main__":
    unittest.main()
