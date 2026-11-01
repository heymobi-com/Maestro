"""The brief box is where the failures start, so it is checked before a run costs anything.

Every measured failure in this pipeline traces to the one input a person writes freely:
a lock the compiler ignores, a DESCRIPTION with no beats so no clip has a story position,
a camera habit applied to every shot, a character in the cast that no stage places. The
assistant names those in the code's own terms, and can rewrite the text into the shape
the selected workflow reads — with the person's own ideas, names and images kept.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.brief_assistant import (  # noqa: E402
    CANONICAL_SECTIONS,
    diagnose_brief,
    normalized_brief,
    strip_template_placeholders,
    structure_prompt,
)
from services.director.planners.short_film import _brief_stage_list  # noqa: E402

PROSE_BRIEF = """Abuela Rosa is an old woman who waits in a stone kitchen. One night the
children do not come home. She walks a corridor where a tall figure in a black shroud waits.
The shroud opens and a priest is revealed. She kneels in the church and the bell rings.
"""

GOOD_BRIEF = """SUBJECT LOCK:
<Subject 1> is ALWAYS Abuela Rosa.
<Subject 2> is ALWAYS la Figura del Purgatorio.
Don Aurelio never becomes S1 or S2.

CHARACTERS:
Abuela Rosa: woman, 78, black headscarf, grey cardigan.
La Figura del Purgatorio: tall figure wrapped in a black shroud, erased face.

CAMERA (per stage):
- Stage 1: locked-off and symmetrical.
- Stage 2: handheld behind Abuela Rosa.

AMBIENCE:
Stone mountain village in fog, cold blue palette.

DESCRIPTION:
Stage 1 (kitchen): Abuela Rosa at the wooden table, a knife in the wood.
Stage 2 (corridor): the figure enters behind her, bells swaying.
Stage 3 (church): Don Aurelio revealed, Abuela Rosa kneels.

NEGATIVE:
No extra characters, no gore, no generated speech.
"""


class ProseBriefTests(unittest.TestCase):
    def setUp(self):
        self.report = diagnose_brief(
            PROSE_BRIEF, kind="short_film_audio", clip_count=26,
            transcript_speakers=["S1", "S2"], has_audio=True,
        )
        self.codes = {finding["code"] for finding in self.report["findings"]}

    def test_a_prose_brief_is_told_it_has_no_parseable_lock(self):
        self.assertIn("no-subject-lock", self.codes)

    def test_it_is_told_the_audio_speakers_are_unbound(self):
        self.assertIn("speakers-unbound", self.codes)

    def test_every_finding_carries_a_fix(self):
        for finding in self.report["findings"]:
            self.assertTrue(finding["fix"].strip(), finding["code"])
            self.assertIn(finding["severity"], {"error", "warning", "info"})

    def test_errors_come_before_warnings(self):
        order = {"error": 0, "warning": 1, "info": 2}
        severities = [order[f["severity"]] for f in self.report["findings"]]

        self.assertEqual(severities, sorted(severities))

    def test_the_report_names_the_workflow_it_judged_against(self):
        self.assertEqual(self.report["kind"], "short_film_audio")
        self.assertIn("short film", self.report["label"])


class WorkflowSpecificTests(unittest.TestCase):
    def test_a_podcast_is_not_asked_for_stages(self):
        report = diagnose_brief(GOOD_BRIEF, kind="podcast", clip_count=10)
        codes = {finding["code"] for finding in report["findings"]}

        self.assertNotIn("no-stages", codes)
        self.assertIn("podcast-stages", codes)

    def test_a_short_film_without_beats_is_an_error(self):
        report = diagnose_brief("CHARACTERS:\nRosa: woman.\n", kind="short_film_story", clip_count=20)
        codes = {finding["code"] for finding in report["findings"]}

        self.assertIn("no-stages", codes)

    def test_a_music_video_is_asked_about_song_sections(self):
        report = diagnose_brief("CHARACTERS:\nSinger: tall.\n", kind="music_video", clip_count=12)
        codes = {finding["code"] for finding in report["findings"]}

        self.assertIn("music-no-sections", codes)


class FaultDetectionTests(unittest.TestCase):
    def test_a_camera_habit_is_named_with_the_word_that_causes_it(self):
        brief = GOOD_BRIEF.replace(
            "- Stage 2: handheld behind Abuela Rosa.",
            "Push in on Abuela Rosa whenever she is watched.",
        )
        report = diagnose_brief(brief, kind="short_film_audio", clip_count=20)
        finding = next(f for f in report["findings"] if f["code"] == "camera-habit")

        self.assertIn("whenever", finding["message"])

    def test_a_stage_that_wanders_is_named(self):
        brief = GOOD_BRIEF.replace(
            "Stage 3 (church): Don Aurelio revealed, Abuela Rosa kneels.",
            "Stage 3 (wander): the church, the corridor, the street and the forest.",
        )
        report = diagnose_brief(brief, kind="short_film_audio", clip_count=20)
        codes = {finding["code"] for finding in report["findings"]}

        self.assertIn("stage-wanders", codes)

    def test_a_cast_row_no_stage_places_is_named(self):
        brief = GOOD_BRIEF.replace(
            "La Figura del Purgatorio: tall figure wrapped in a black shroud, erased face.",
            "La Figura del Purgatorio: tall figure wrapped in a black shroud, erased face.\n"
            "Don Aurelio: man, 60, worn yellowish alb.",
        ).replace("Don Aurelio revealed, ", "")
        report = diagnose_brief(brief, kind="short_film_audio", clip_count=20)
        finding = next(f for f in report["findings"] if f["code"] == "cast-without-stage")

        self.assertIn("Don Aurelio", finding["message"])

    def test_every_unplaced_cast_row_is_listed(self):
        brief = GOOD_BRIEF.replace(
            "CHARACTERS:\n",
            "CHARACTERS:\nOtra Mujer: woman, 40.\nOtra Hombre: man, 30.\n",
        ).replace("Don Aurelio revealed, ", "")
        report = diagnose_brief(brief, kind="short_film_audio", clip_count=20)
        finding = next(f for f in report["findings"] if f["code"] == "cast-without-stage")

        self.assertIn("3 cast entries", finding["message"])

    def test_a_good_brief_has_no_errors(self):
        report = diagnose_brief(GOOD_BRIEF, kind="short_film_audio", clip_count=20)

        self.assertEqual(
            [f["code"] for f in report["findings"] if f["severity"] == "error"], [],
        )

    def test_an_empty_brief_is_one_clear_finding(self):
        report = diagnose_brief("", kind="short_film_audio", clip_count=20)

        self.assertEqual([f["code"] for f in report["findings"]], ["empty-brief"])

    def test_a_town_named_once_is_not_reported_as_an_undescribed_character(self):
        brief = GOOD_BRIEF.replace(
            "Stage 1 (kitchen): Abuela Rosa at the wooden table, a knife in the wood.",
            "Stage 1 (kitchen): One night in Valdecuna, Abuela Rosa at the table.",
        )
        report = diagnose_brief(brief, kind="short_film_audio", clip_count=20)
        messages = " ".join(f["message"] for f in report["findings"])

        self.assertNotIn("Valdecuna", messages)

    def test_a_person_the_story_acts_with_is_reported(self):
        brief = GOOD_BRIEF.replace(
            "Stage 3 (church): Abuela Rosa kneels.",
            "Stage 3 (church): Tomas calls and Abuela Rosa turns to Tomas.",
        )
        report = diagnose_brief(brief, kind="short_film_audio", clip_count=20)
        codes = {f["code"] for f in report["findings"]}

        self.assertIn("undescribed-character", codes)


class NormalizationTests(unittest.TestCase):
    def test_the_sections_are_put_in_the_canonical_order(self):
        shuffled = (
            "NEGATIVE:\nNo gore.\n\n"
            "DESCRIPTION:\nStage 1 (kitchen): Rosa sits.\n\n"
            "CHARACTERS:\nRosa: woman, 78.\n"
        )
        out = normalized_brief(shuffled, kind="short_film_audio")
        positions = [out.index(name) for name in ("CHARACTERS", "DESCRIPTION", "NEGATIVE")]

        self.assertEqual(positions, sorted(positions))

    def test_a_brief_with_no_beats_gets_numbered_ones(self):
        prose = (
            "CHARACTERS:\nRosa: woman, 78.\n\n"
            "DESCRIPTION:\nOne night in Valdecuna. Kitchen: Rosa at the table, a knife in "
            "the wood. Street: children in masks walk in silence. Corridor: the figure "
            "enters behind Rosa. Church: Rosa kneels in tears.\n"
        )
        out = normalized_brief(prose, kind="short_film_audio")

        self.assertIn("Stage 1", out)
        stages = _brief_stage_list(out)
        self.assertGreaterEqual(len(stages), 4)

    def test_the_planner_can_read_the_result(self):
        out = normalized_brief(PROSE_BRIEF, kind="short_film_audio")

        self.assertTrue(_brief_stage_list(out))

    def test_a_podcast_is_not_given_stages(self):
        out = normalized_brief("DESCRIPTION:\nThe hosts talk about memory.\n", kind="podcast")

        self.assertNotIn("Stage 1", out)

    def test_the_ideas_are_never_rewritten(self):
        out = normalized_brief(PROSE_BRIEF, kind="short_film_audio")

        for name in ("Abuela Rosa", "shroud", "priest", "bell"):
            self.assertIn(name, out)

    def test_the_sections_the_brief_has_are_all_named(self):
        out = normalized_brief(GOOD_BRIEF, kind="short_film_audio")

        self.assertTrue(_brief_stage_list(out))
        for section in ("SUBJECT LOCK", "CHARACTERS", "CAMERA", "AMBIENCE", "DESCRIPTION", "NEGATIVE"):
            self.assertIn(section, out)


class TemplateLeakTests(unittest.TestCase):
    """The assistant must not hand back its own instructions as if they were content."""

    LEAKED = (
        "SUBJECT LOCK:\n"
        "<Subject 1> is ALWAYS Anita.\n"
        "<every other character>: named as presences and objects that never become S1 or S2.\n"
        "\n"
        "CHARACTERS:\n"
        "Anita: una mujer joven, narradora del documental.\n"
        "\n"
        "PRESENCES (only what this workflow needs):\n"
        "David: presence in the biblical action stages.\n"
        "\n"
        "DESCRIPTION:\n"
        "Stage 1 (beat 1): Anita narrates the story of David and Goliath in a documentary.\n"
        "Stage 2 (beat 2): Epic biblical scenes visually illustrate what is being narrated.\n"
        "Stage 3 (beat 3): David walks through the camp and lifts a stone from the stream.\n"
    )

    def _codes(self) -> set[str]:
        return {
            finding["code"]
            for finding in diagnose_brief(self.LEAKED, kind="short_film_story", clip_count=42)["findings"]
        }

    def test_a_placeholder_line_is_an_error(self):
        self.assertIn("template-placeholder", self._codes())

    def test_a_beat_that_only_describes_the_format_is_reported(self):
        self.assertIn("abstract-beats", self._codes())

    def test_a_presence_that_acts_without_an_appearance_is_reported(self):
        self.assertIn("cast-without-appearance", self._codes())

    def test_a_presence_the_story_never_uses_is_not_reported(self):
        brief = (
            "CHARACTERS:\nAnita: narrator in a cream shirt.\n\n"
            "PRESENCES:\nA Crowd: stands at the back, out of focus.\n\n"
            "DESCRIPTION:\n"
            "Stage 1 (kitchen): Anita walks to the wooden table and lifts the cup.\n"
        )

        codes = {
            finding["code"]
            for finding in diagnose_brief(brief, kind="short_film_story", clip_count=8)["findings"]
        }
        self.assertNotIn("cast-without-appearance", codes)

    def test_the_placeholder_line_is_removed(self):
        cleaned = strip_template_placeholders(self.LEAKED)

        self.assertNotIn("<every other character>", cleaned)
        self.assertIn("<Subject 1> is ALWAYS Anita", cleaned)

    def test_the_instruction_heading_keeps_its_section_and_loses_the_note(self):
        cleaned = strip_template_placeholders(self.LEAKED)

        self.assertIn("PRESENCES:", cleaned)
        self.assertNotIn("only what this workflow needs", cleaned)

    def test_the_prompt_forbids_placeholders_in_the_answer(self):
        system, _user = structure_prompt(self.LEAKED, kind="short_film_story", clip_count=42)

        self.assertIn("Never output a line whose key is in angle brackets", system)
        self.assertIn('only describes the format ("epic scenes illustrate', " ".join(system.split()))

    def test_the_prompt_asks_for_one_named_row_per_character(self):
        system, _ = structure_prompt(self.LEAKED, kind="short_film_story", clip_count=42)

        self.assertIn("Every character the story shows by name gets its own line", system)

    def test_a_concrete_stage_is_not_reported_as_abstract(self):
        brief = (
            "CHARACTERS:\nAnita: narrator in a cream shirt.\n\n"
            "DESCRIPTION:\n"
            "Stage 1 (kitchen): Anita walks to the wooden table and lifts the cup.\n"
            "Stage 2 (street): Anita crosses the cobbled street and turns to the crowd.\n"
            "Stage 3 (church): Anita kneels at the altar and prays.\n"
        )

        self.assertNotIn(
            "abstract-beats",
            {f["code"] for f in diagnose_brief(brief, kind="short_film_story", clip_count=12)["findings"]},
        )


class StructurePromptTests(unittest.TestCase):
    def test_the_prompt_forbids_inventing_a_character(self):
        system, _user = structure_prompt(PROSE_BRIEF, kind="short_film_audio", clip_count=26)

        self.assertIn("Never invent a character", system)

    def test_the_prompt_carries_the_workflow_contract(self):
        system, _ = structure_prompt(GOOD_BRIEF, kind="podcast", clip_count=10)

        self.assertIn("PODCAST", system)

    def test_the_prompt_lists_the_problems_to_fix(self):
        report = diagnose_brief(PROSE_BRIEF, kind="short_film_audio", clip_count=26)
        _system, user = structure_prompt(
            PROSE_BRIEF, kind="short_film_audio", diagnosis=report, clip_count=26,
        )

        self.assertIn("no-subject-lock", user)

    def test_the_prompt_hands_over_the_original_text(self):
        _system, user = structure_prompt(PROSE_BRIEF, kind="short_film_audio", clip_count=26)

        self.assertIn("Abuela Rosa is an old woman", user)

    def test_the_speakers_reach_the_prompt(self):
        _system, user = structure_prompt(
            PROSE_BRIEF, kind="short_film_audio", clip_count=26,
            transcript_speakers=["S1", "S2"],
        )

        self.assertIn("S1, S2", user)


if __name__ == "__main__":
    unittest.main()
