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
    narration_presence,
    narrator_from_context,
    scene_guidance,
    scene_policy,
    spread_staging,
    staging_report,
    staging_rules,
)

PLANNERS = APP / "services/director/planners"


class StagingTests(unittest.TestCase):
    """Presence is not enough: the narrator must share the action's depth and scale.

    Measured on a real 36-clip run, 30 of the openings put the narrator in the foreground
    and 8 pushed the described figures into the "distant background". Every clip had its
    own place and its own line, and the film still read as one presenter shot repeated ?
    which is what the person reported as "una copia de la misma escena".
    """

    BRIEF = (
        "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
        "DESCRIPTION:\nAnita walks the audience through the ancient landscape, showing "
        "the characters what is happening.\n"
    )

    def test_the_narrator_is_at_the_scale_of_what_she_describes(self):
        text = narration_presence(self.BRIEF)

        self.assertIn("SAME DEPTH AND SCALE", text)
        self.assertIn("never small in the distant background", text)

    def test_the_default_presenter_framing_is_named_as_wrong(self):
        text = narration_presence(self.BRIEF)

        self.assertIn('Do not write "in the foreground center" as a default composition', text)
        self.assertIn("No more than one shot in three", text)

    def test_the_shot_rule_is_compact(self):
        text = narration_presence(self.BRIEF, compact=True)

        self.assertIn("SAME DEPTH AND SCALE", text)
        self.assertLess(len(text), 620)

    def test_the_staging_rule_works_without_a_name(self):
        text = staging_rules("")

        self.assertIn("SAME DEPTH AND SCALE", text)
        self.assertNotIn("None", text)

    def test_a_plan_of_one_repeated_framing_is_reported(self):
        openings = ["Anita stands in the foreground center, facing the camera."] * 6

        findings = staging_report(openings, name="Anita")

        self.assertTrue(any("in the foreground" in item for item in findings))
        self.assertTrue(any("facing the camera" in item for item in findings))

    def test_figures_pushed_into_the_distance_are_reported(self):
        openings = [
            "Anita in the foreground center. Goliath stands small in the distant background right.",
            "Anita in the foreground center. David in the distant background left.",
            "Anita walks among the soldiers, spears at her scale.",
            "Low angle past Anita up to Goliath filling the frame.",
            "Close on David's hands, Anita kneeling beside him.",
        ]

        findings = staging_report(openings, name="Anita")

        self.assertTrue(any("distant" in item for item in findings))

    def test_a_varied_plan_raises_nothing(self):
        openings = [
            "Anita walks among the soldiers, the spears at her scale on both sides.",
            "Low angle past Anita up to Goliath, who fills the frame above her.",
            "Close on David's hands on the sling, Anita kneeling beside him.",
            "Wide of the camp with Anita small between the tents, pointing at the fires.",
            "Over Anita's shoulder at King Saul slumping on his throne.",
            "Anita strides through the crowd, the people at her scale around her.",
        ]

        self.assertEqual(staging_report(openings, name="Anita"), [])

    def test_too_few_shots_to_judge(self):
        self.assertEqual(staging_report(["Anita in the foreground center."] * 3), [])


class StagingRepairTests(unittest.TestCase):
    """The plan is repaired, not merely reported: reporting alone costs a render first."""

    def test_a_figure_in_the_distance_is_brought_to_her_scale(self):
        fixed = spread_staging(
            ["Anita in foreground center. Goliath stands towering in the distant background right."],
            name="Anita",
        )

        self.assertIn("right behind her on the right, at her scale", fixed[0])
        self.assertNotIn("distant", fixed[0])

    def test_the_repair_reads_as_english(self):
        fixed = spread_staging(
            ["Anita stands in the foreground. King Saul stands in the distant background "
             "inside his royal tent, holding his spear."],
            name="Anita",
        )

        self.assertIn("King Saul stands right behind her, at her scale inside his royal tent", fixed[0])
        self.assertNotIn("scaleinside", fixed[0])

    def test_a_figure_beside_her_in_the_background_moves_to_her_plane(self):
        fixed = spread_staging(
            ["Anita stands in the foreground, David in the background left, Goliath in the "
             "background right."],
            name="Anita",
        )

        self.assertIn("David beside her on the left", fixed[0])
        self.assertIn("Goliath beside her on the right", fixed[0])

    def test_a_run_of_presenter_framings_is_spread(self):
        same = ["Anita stands in the foreground center, facing the camera."] * 12
        fixed = spread_staging(same, name="Anita", environments=["the valley"] * 12)

        self.assertGreaterEqual(len(set(fixed)), 5)
        self.assertNotIn(same[0], fixed[1:])

    def test_the_presenter_framing_survives_a_share_of_the_shots(self):
        same = ["Anita stands in the foreground center, facing the camera."] * 12
        fixed = spread_staging(same, name="Anita")

        self.assertIn(same[0], fixed)

    def test_the_figures_a_re_framed_shot_names_are_kept(self):
        opening = "Anita in foreground center. King Saul stands beside her on the left."
        fixed = spread_staging([opening] * 12, name="Anita")

        self.assertNotIn("foreground", fixed[-1])
        self.assertIn("King Saul stands beside her on the left", fixed[-1])

    def test_a_clean_plan_is_untouched_byte_for_byte(self):
        clean = [
            "Anita walks among the soldiers, the spears at her scale on both sides.",
            "Low angle past Anita up to Goliath, who fills the frame above her.",
            "Close on David's hands on the sling, Anita kneeling beside him.",
            "Wide of the camp with Anita small between the tents, pointing at the fires.",
        ]

        self.assertEqual(spread_staging(clean, name="Anita"), clean)

    def test_the_repair_is_idempotent(self):
        plan = ["Anita in foreground center. Goliath stands towering in the distant background right."]

        once = spread_staging(plan, name="Anita")

        self.assertEqual(spread_staging(once, name="Anita"), once)

    def test_the_narrator_is_read_from_a_shot_contract(self):
        context = "Anita (a woman) is ON SCREEN in this shot, inside the scene."

        self.assertEqual(narrator_from_context(context), "Anita")

    def test_the_narrator_is_read_from_a_lock(self):
        self.assertEqual(
            narrator_from_context("SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n"), "Anita",
        )


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


class NarrationPresenceTests(unittest.TestCase):
    """A brief that puts the narrator inside the film must reach the planner as such."""

    BRIEF = (
        "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
        "DESCRIPTION:\nSTANDING RULE: Anita moves immersively through the settings and "
        "is at the center of the cinematic action in every shot.\n"
    )

    def test_a_presenter_brief_is_recognised(self):
        from services.director.scene_policy import narration_is_present

        self.assertTrue(narration_is_present(self.BRIEF))

    def test_a_story_brief_is_not(self):
        from services.director.scene_policy import narration_is_present

        self.assertFalse(narration_is_present(
            "SUBJECT LOCK:\n<Subject 1> is ALWAYS Rosa.\n\nDESCRIPTION:\nStage 1: kitchen."
        ))

    def test_a_brief_that_only_shows_the_narrator_doing_things_is_recognised(self):
        # The measured real brief said none of the rule phrases: "Anita walks the
        # audience through the landscape", "Anita stands near him".
        from services.director.scene_policy import narration_is_present

        brief = (
            "SUBJECT LOCK: <Subject 1> (S1) is ALWAYS Anita. "
            "DESCRIPTION: Stage 1 (Valley Overview): Anita walks the audience as a modern "
            "guide in the ancient landscape. Stage 2: David prepares his sling; Anita walks "
            "beside him. Stage 3: Goliath steps forward; Anita stands near him."
        )

        self.assertTrue(narration_is_present(brief))

    def test_a_secondary_character_walking_does_not_make_them_a_presenter(self):
        from services.director.scene_policy import narration_is_present

        brief = (
            "SUBJECT LOCK:\n<Subject 1> is ALWAYS Rosa.\n\n"
            "DESCRIPTION:\nStage 1: Anita walks through the market and Rosa lifts the cup.\n"
        )

        self.assertFalse(narration_is_present(brief))

    def test_off_camera_wording_is_repaired_for_a_presenter(self):
        from services.director.scene_policy import presenter_voiceover_repair

        prompt = (
            "Anita (S1) says in an off-screen voiceover that four giants existed, "
            "while her lips remain completely closed. She opens the codex."
        )
        brief = (
            "SUBJECT LOCK: <Subject 1> (S1) is ALWAYS Anita. "
            "DESCRIPTION: Anita walks the audience through the valley."
        )

        repaired = presenter_voiceover_repair(brief, prompt)

        self.assertNotIn("voiceover", repaired.lower())
        self.assertNotIn("completely closed", repaired.lower())
        self.assertIn("on camera", repaired)
        self.assertIn("She opens the codex.", repaired)

    def test_the_repair_leaves_a_real_voiceover_alone(self):
        from services.director.scene_policy import presenter_voiceover_repair

        prompt = "Anita (S1) says in an off-screen voiceover that four giants existed."
        brief = (
            "SUBJECT LOCK: <Subject 1> (S1) is ALWAYS Anita.\n\n"
            "DESCRIPTION:\nStage 1: Rosa lifts the cup in the kitchen.\n"
        )

        self.assertEqual(presenter_voiceover_repair(brief, prompt), prompt)

    def test_the_name_comes_from_the_lock(self):
        from services.director.scene_policy import narrator_name

        self.assertEqual(narrator_name(self.BRIEF), "Anita")

    def test_the_block_forbids_voiceover_for_the_presenter(self):
        from services.director.scene_policy import narration_presence

        block = narration_presence(self.BRIEF)

        self.assertIn("NARRATION PRESENCE", block)
        self.assertIn("ON CAMERA", block)
        self.assertIn("off-screen voiceover", block)
        self.assertIn("subjects_on_screen", block)

    def test_no_block_for_a_film_without_a_presenter(self):
        from services.director.scene_policy import narration_presence

        self.assertEqual(
            narration_presence("Stage 1: Rosa lifts the cup in the kitchen."), ""
        )


if __name__ == "__main__":
    unittest.main()
