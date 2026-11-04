"""One shot reads one stage of the brief, never the whole cast list.

Measured on a real project: every compiled clip carried the entire brief — the SUBJECT
LOCK, the full cast with each character's wardrobe, and all seven stages. Clip 1, a
village street at dusk, carried "S3 (Don Aurelio): man, 60 years old … worn yellowish
alb, faded chasuble", so the priest was rendered in scene after scene even after his
reference image was removed from the manifest. Whatever repeats in every prompt is what
the model draws in every shot.

The brief says the shroud opens onto Don Aurelio once, in Stage 5. That is where he
belongs, and after this only that stage carries his description.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.h3_dialogue import project_subject_lock  # noqa: E402
from services.director_pipeline import (  # noqa: E402
    _h3_shot_project_context,
    _h3_world_context,
    _h3_cast_naming_report,
    _h3_plan_contract_issues,
    _repair_h3_staging,
    _h3_staging_report,
)

BRIEF = (
    "SUBJECT LOCK (critical, non-negotiable):\n"
    "- Subject 1 is ALWAYS Abuela Rosa. Subject 2 is ALWAYS la Figura del Purgatorio.\n"
    "- S3-S7 (Don Aurelio, the five children, the villagers) are presence-only.\n"
    "\n"
    "CHARACTERS AND BINDINGS:\n"
    "S1 (Abuela Rosa): woman, 78 years old, black headscarf, grey cardigan.\n"
    "S2 (La Figura del Purgatorio): tall figure wrapped in a black shroud.\n"
    "S3 (Don Aurelio): man, 60 years old, worn yellowish alb, faded chasuble.\n"
    "\n"
    "AMBIENCE:\n"
    "- A stone mountain village at the edge of a dark forest, fog without rain,\n"
    "  cobbled streets, slate roofs, pumpkin lanterns.\n"
    "\n"
    "DESCRIPTION:\n"
    "Stage 1 (fog gathering): dawn-grey kitchen, S1 stands before a wooden table. "
    "Stage 2 (the town waits): a cobbled street, children in paper masks. "
    "Stage 3 (the veil tearing): the black shroud opens, revealing Don Aurelio. "
    "Stage 4 (the closed door): the corridor again, the door now a chapel.\n"
)


class WorldContextTests(unittest.TestCase):
    def test_the_ambience_is_kept(self):
        world = _h3_world_context(BRIEF)

        self.assertIn("stone mountain village", world)
        self.assertIn("slate roofs", world)

    def test_the_cast_list_is_not_part_of_the_world(self):
        world = _h3_world_context(BRIEF)

        self.assertNotIn("Aurelio", world)
        self.assertNotIn("chasuble", world)

    def test_the_world_is_bounded(self):
        long_brief = "AMBIENCE:\n- " + ("fog " * 4000)

        self.assertLessEqual(len(_h3_world_context(long_brief)), 700)


class ShotContextTests(unittest.TestCase):
    def test_a_clip_outside_his_stage_never_learns_the_priest_exists(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertNotIn("Aurelio", context)
        self.assertNotIn("chasuble", context)

    def test_the_stage_list_never_reaches_a_shot(self):
        # The stage restated the film's beat list inside every clip, and contradicted the
        # shot: a measured clip's context said "Locked-off wide shot of the valley" while
        # its own description said "Medium static shot of Anita". The shot is the authority
        # on what it shows.
        contexts = [_h3_shot_project_context(BRIEF, index, 8) for index in range(8)]

        for context in contexts:
            self.assertNotIn("fog gathering", context)
            self.assertNotIn("the closed door", context)
            self.assertNotIn("Aurelio", context)

    def test_the_lock_survives_in_the_shape_the_compiler_parses(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertEqual(project_subject_lock(context), {1: "Abuela", 2: "Figura"})

    def test_the_cast_enumeration_beside_the_lock_is_dropped(self):
        context = _h3_shot_project_context(BRIEF, 0, 8)

        self.assertNotIn("presence-only", context)

    def test_a_brief_with_no_stages_keeps_the_world(self):
        context = _h3_shot_project_context("AMBIENCE:\n- a frozen lake.", 0, 4)

        self.assertIn("frozen lake", context)
        self.assertFalse(context.startswith("\n"))

    def test_a_brief_with_nothing_usable_is_empty(self):
        self.assertEqual(_h3_shot_project_context("A woman waits.", 0, 4), "")

    def test_the_on_screen_narrator_reaches_the_shot(self):
        brief = (
            "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
            "DESCRIPTION:\nSTANDING RULE: Anita is at the center of the cinematic "
            "action in every shot, moving immersively through the settings.\n"
        )

        context = _h3_shot_project_context(brief, 0, 8)

        self.assertIn("ON SCREEN", context)
        self.assertIn("Anita", context)
        self.assertIn("off-screen voiceover", context)

    def test_the_presence_rule_is_compact_because_it_repeats_in_every_shot(self):
        # The long form was 1,005 characters of identical text against a 734-character
        # scene, which is how forty clips become one shot.
        brief = (
            "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
            "DESCRIPTION:\nSTANDING RULE: Anita moves through the settings at the center "
            "of the cinematic action.\n"
        )

        self.assertLess(len(_h3_shot_project_context(brief, 0, 8)), 700)

    def test_the_whole_context_stays_smaller_than_a_real_scene(self):
        context = _h3_shot_project_context(BRIEF, 0, 26)

        self.assertLess(len(context), 734)

    def test_a_film_without_a_presenter_gets_no_presence_rule(self):
        brief = (
            "SUBJECT LOCK:\n<Subject 1> is ALWAYS Rosa.\n\n"
            "DESCRIPTION:\nStage 1: Rosa lifts the cup in the kitchen.\n"
        )

        self.assertNotIn("ON SCREEN", _h3_shot_project_context(brief, 0, 8))


class DilutionTests(unittest.TestCase):
    """The failure the user paid for: forty clips, one shot, different audio."""

    def test_boilerplate_that_outweighs_the_scene_is_reported(self):
        from services.director_pipeline import _h3_prompt_dilution_report

        plans = [
            {"_director_project_context": "x" * 2900, "_director_h3_source_prompt": "y" * 700}
            for _ in range(6)
        ]

        findings = _h3_prompt_dilution_report(plans, pid="t")

        self.assertTrue(any("boilerplate" in line for line in findings))

    def test_a_lean_context_is_not_reported(self):
        from services.director_pipeline import _h3_prompt_dilution_report

        plans = [
            {"_director_project_context": "x" * 400, "_director_h3_source_prompt": "y" * 800}
            for _ in range(6)
        ]

        self.assertEqual(_h3_prompt_dilution_report(plans, pid="t"), [])

    def test_nearly_identical_compiled_prompts_are_reported(self):
        from services.director_pipeline import _h3_prompt_dilution_report

        shared = " ".join(f"word{index}" for index in range(120))
        plans = [
            {
                "_director_h3_compiled_prompt": f"{shared} unique{index}",
                "_director_project_context": "x" * 100,
                "_director_h3_source_prompt": "y" * 800,
            }
            for index in range(5)
        ]

        findings = _h3_prompt_dilution_report(plans, pid="t")

        self.assertTrue(any("nearly the same prompt" in line for line in findings))

    def test_distinct_compiled_prompts_are_not_reported(self):
        from services.director_pipeline import _h3_prompt_dilution_report

        plans = [
            {
                "_director_h3_compiled_prompt": " ".join(
                    f"clip{index}word{word}" for word in range(120)
                ),
                "_director_project_context": "x" * 100,
                "_director_h3_source_prompt": "y" * 800,
            }
            for index in range(5)
        ]

        self.assertEqual(_h3_prompt_dilution_report(plans, pid="t"), [])


class ProtectedContextTests(unittest.TestCase):
    """The compaction pass may trim boilerplate, never the rules a shot needs."""

    def test_the_context_survives_compaction(self):
        from services.h3_prompt_budget import fit_h3_base_prompt, h3_prompt_token_count
        from services.director.scene_policy import narration_presence

        presence = narration_presence(
            "STANDING RULE: Anita moves through the settings at the center of the "
            "cinematic action."
        )
        boilerplate = (
            "Keep every requested subject's identity, appearance, wardrobe, and carried "
            "objects unchanged. "
        )
        filler = "The environment contains pale limestone, dry grass and a low wall. " * 120
        body = (
            "integrated_multimodal_description: [Shot 1] Anita stands. "
            + boilerplate + presence + " " + filler
            + "\n\noverall_soundscape: wind.\n\nnon_diegetic_music: N/A"
        )

        result = fit_h3_base_prompt(body, target_tokens=1024, protect=presence)

        self.assertGreater(h3_prompt_token_count(body), 1024)
        self.assertTrue(result.compacted)
        self.assertIn(presence.strip(), result.prompt)
        self.assertNotIn(boilerplate.strip(), result.prompt)


class StagingRepairInThePlanTests(unittest.TestCase):
    """The whole plan is repaired before a prompt is compiled, not just reported."""

    def _plans(self, openings):
        context = "Anita (a woman) is ON SCREEN in this shot, inside the scene."
        return [
            {
                "_director_project_context": context,
                "_director_environment": "the valley of Elah",
                "_director_opening_blocking": opening,
            }
            for opening in openings
        ]

    def test_the_plan_the_figures_were_pushed_away_from_is_repaired(self):
        plans = self._plans([
            "Anita in foreground center. Goliath stands towering in the distant background right.",
            "Anita in foreground center. David stands small in the distant background left.",
            "Anita in foreground center. King Saul stands in the distant background.",
            "Anita in foreground center. The soldiers stand in the distant background right.",
        ])

        changed = _repair_h3_staging(plans)

        self.assertEqual(changed, 4)
        self.assertFalse(any(
            "distant" in str(plan["_director_opening_blocking"]) for plan in plans
        ))
        self.assertEqual(_h3_staging_report(plans), [])

    def test_the_repair_is_idempotent(self):
        plans = self._plans([
            "Anita in foreground center. Goliath stands towering in the distant background right."
        ])

        _repair_h3_staging(plans)

        self.assertEqual(_repair_h3_staging(plans), 0)

    def test_a_varied_plan_is_left_alone(self):
        openings = [
            "Anita walks among the soldiers, the spears at her scale on both sides.",
            "Low angle past Anita up to Goliath, who fills the frame above her.",
            "Close on David's hands, Anita kneeling beside him.",
            "Wide of the camp, Anita small between the tents, pointing at the fires.",
        ]
        plans = self._plans(openings)

        self.assertEqual(_repair_h3_staging(plans), 0)
        self.assertEqual([plan["_director_opening_blocking"] for plan in plans], openings)


class CastNamingTests(unittest.TestCase):
    """A plan that writes its own people instead of the cast is reported at plan time.

    The person reviews the shots before rendering, which is exactly when this has to be
    said: their good plan names Anita in 36 of 36 prompts; the failing one described
    "the teen boy in earthy brown" and "the narrator man in dark grey".
    """

    CONTEXT = "Anita (a woman) is ON SCREEN in this shot, inside the scene."

    def _plan(self, prompt):
        return {
            "_director_project_context": self.CONTEXT,
            "_director_subjects_on_screen": [
                {"visual_description": "<Picture 1> Anita (S1) \u2014 Woman, late twenties."},
            ],
            "_director_h3_source_prompt": prompt,
        }

    def test_a_plan_that_names_the_cast_is_silent(self):
        plans = [self._plan("Anita walks among the soldiers, at their scale.") for _ in range(4)]

        self.assertEqual(_h3_cast_naming_report(plans), [])

    def test_a_plan_that_invents_its_people_is_reported(self):
        plans = [
            self._plan("The teen boy in earthy brown stands facing the giant man in bronze armor.")
            for _ in range(4)
        ]
        findings = _h3_cast_naming_report(plans)

        self.assertEqual(len(findings), 1)
        self.assertIn("4 of 4", findings[0])
        self.assertIn("Anita", findings[0])

    def test_a_few_unnamed_shots_are_tolerated(self):
        plans = [self._plan("Anita walks among the soldiers.") for _ in range(4)]
        plans[0] = self._plan("A wide shot of the empty valley at dawn.")

        self.assertEqual(_h3_cast_naming_report(plans), [])


class PlanContractTests(unittest.TestCase):
    """A plan that ignored the audio and the cast is refused before any GPU work.

    Shaped after a real storyboard: every shot carried a "dialogue" section, one speaker,
    and no spoken line, and the prompts described people the project had already cast
    ("the teen boy in earthy brown", "the narrator man in dark grey") instead of
    <Subject N> / <Picture N>.
    """

    def _bad(self):
        return [
            {
                "planned_clip": {"dialogue_lines": ["a", "b"]},
                "_director_subjects_on_screen": [{"name": "Anita"}],
                "_director_speaker_registry": {"(S)": ""},
                "video_prompt": (
                    "detailed_description: (0-10s): The teen boy in earthy brown stands "
                    "facing the giant man in bronze armor."
                ),
            },
        ]

    def test_a_silent_shot_with_speech_in_its_window_is_reported(self):
        issues = _h3_plan_contract_issues(self._bad())

        self.assertTrue(any("spoken line" in item for item in issues))

    def test_a_shot_that_ignores_the_cast_is_reported(self):
        issues = _h3_plan_contract_issues(self._bad())

        self.assertTrue(any("<Subject N>" in item for item in issues))

    def test_a_bare_speaker_label_is_reported(self):
        issues = _h3_plan_contract_issues(self._bad())

        self.assertTrue(any("names no participant" in item for item in issues))

    def test_a_good_plan_raises_nothing(self):
        plan = [
            {
                "planned_clip": {"dialogue_lines": ["a"]},
                "_director_subjects_on_screen": [{"name": "Anita"}],
                "_director_speaker_registry": {"(S1)": "Anita"},
                "video_prompt": (
                    "subject_definitions: <Subject 1> (S1) is Anita, using <Picture 1>. "
                    "detailed_description: [Shot 1] <Subject 1> says: <d>Here it is.</d>"
                ),
            },
            {
                "planned_clip": {"dialogue_lines": []},
                "_director_subjects_on_screen": [],
                "_director_speaker_registry": {"(S1)": "Anita"},
                "video_prompt": "detailed_description: wide shot of the valley, no speech.",
            },
        ]

        self.assertEqual(_h3_plan_contract_issues(plan), [])


if __name__ == "__main__":
    unittest.main()
