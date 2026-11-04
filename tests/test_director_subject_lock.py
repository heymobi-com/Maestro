"""One reader for the SUBJECT LOCK block, because three readers disagreed.

A brief arrived with its lock decorated by the picture that supplies each character's
identity:

    SUBJECT LOCK: <Subject 1> (S1)(Picture 1) is ALWAYS Anita. \
<Subject 2> (S2) (Picture 2)is ALWAYS David. <Subject 3> (S3) (Picture 3) is ALWAYS Goliath.

The compiler's regex, the brief validator's regex and the narrator check each accepted
exactly one parenthetical. So on that brief:

* the lock parsed to nothing and every voice was attributed by row order,
* the planner wrote a nameless ``(S)`` narrator and described her as male,
* ``narration_is_present`` found no narrator name, so the whole film was planned as an
  off-screen voiceover and the one person the documentary is built around appeared in no
  shot.

And ``normalize_speaker_cues``, which exists to repair a wrong cue, rewrote
``(Picture 2)`` into ``(S1)`` because it rewrote whichever parenthetical preceded "is
ALWAYS" — destroying the picture reference it was supposed to protect.
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
    _cast_rows,
    keeps_the_story,
    merge_structured,
    _raw_lock_cues,
    _section_rows,
    diagnose_brief,
    normalize_speaker_cues,
)
from services.director.cast_vision import gender_of, narrator_gender  # noqa: E402
from services.director.h3_dialogue import (  # noqa: E402
    project_subject_lock,
    project_subject_lock_cues,
)
from services.director.scene_policy import (  # noqa: E402
    narration_is_present,
    narration_presence,
    narrator_name,
)
from services.director.subject_lock import (  # noqa: E402
    cues,
    is_canonical_cue,
    parse,
    speakerish_cues,
)

DECORATED = (
    "SUBJECT LOCK: <Subject 1> (S1)(Picture 1) is ALWAYS Anita. "
    "<Subject 2> (S2) (Picture 2)is ALWAYS David. "
    "<Subject 3> (S3) (Picture 3) is ALWAYS Goliath. "
    "King Saul, the Israelite Soldiers, the Philistine Soldiers, and the People of the "
    "Ancient Land never become a Subject. "
    "CHARACTERS: Anita (S1): Young woman, medium build; fair skin; cream off-white "
    "button-up shirt with pointed collar, tucked in, paired with a fitted navy blue "
    "knee-length pencil skirt; black pointed-toe high-heel pumps. "
    "David (S2): Young adult, athletic lean-muscular build; shirtless torso, cream "
    "off-white knee-length tunic belted at waist; brown leather flat sandals. "
    "Goliath (S3): Adult male, 30s-40s, remarkably taller than other characters; bronze "
    "cuirass breastplate with embossed detailing; brown leather flat-soled sandals. "
    "PRESENCES: King Saul: Older king with grey-streaked hair and a full beard, wearing a "
    "fine dark tunic under a heavy wool cloak and a simple bronze circlet on his head; "
    "carries a spear. "
    "The Israelite Soldiers: Lean, sun-darkened men with short dark hair and "
    "full beards, wearing simple knee-length undyed linen tunics. "
    "CAMERA: Eye-level for Anita, low angle for Goliath and the soldiers. "
    "DESCRIPTION: Anita walks the audience through the ancient landscape, explaining the "
    "setting while the armies wait. The Israelite Soldiers gather on the ridge. King "
    "Saul's Tent is shown: Inside the royal tent, "
    "King Saul sits with his spear, grey-streaked hair visible; Anita moves through the "
    "camp. David prepares his sling with grey stones; Anita walks beside him. "
    "NEGATIVE: No biblical figure wears modern clothing."
)


class LockParsingTests(unittest.TestCase):
    def test_a_picture_beside_the_cue_does_not_hide_the_lock(self):
        self.assertEqual(
            parse(DECORATED), {1: "Anita", 2: "David", 3: "Goliath"},
        )

    def test_the_cue_survives_the_picture(self):
        self.assertEqual(cues(DECORATED), {1: "S1", 2: "S2", 3: "S3"})

    def test_the_compiler_reads_the_same_lock(self):
        self.assertEqual(
            project_subject_lock(DECORATED), {1: "Anita", 2: "David", 3: "Goliath"},
        )
        self.assertEqual(project_subject_lock_cues(DECORATED), {1: "S1", 2: "S2", 3: "S3"})

    def test_a_diarizer_id_is_still_reported_as_a_wrong_cue(self):
        brief = "SUBJECT LOCK: <Subject 1> (SPEAKER_00) is ALWAYS Anita."

        self.assertEqual(_raw_lock_cues(brief), {"SPEAKER_00"})
        self.assertEqual(cues(brief), {1: "S1"})
        self.assertIn("lock-cue-not-canonical", _codes(brief))

    def test_a_picture_is_never_reported_as_a_wrong_cue(self):
        self.assertEqual(speakerish_cues("SUBJECT LOCK: <Subject 1> (Picture 1) is ALWAYS Anita."), set())

    def test_canonical_form(self):
        self.assertTrue(is_canonical_cue("S1"))
        self.assertTrue(is_canonical_cue("(S2)".strip("()")))
        self.assertFalse(is_canonical_cue("SPEAKER_00"))
        self.assertFalse(is_canonical_cue("Picture 1"))


class CueNormalizationTests(unittest.TestCase):
    def test_the_picture_reference_is_not_turned_into_a_speaker(self):
        repaired = normalize_speaker_cues(DECORATED)

        self.assertIn("(Picture 1) is ALWAYS Anita", repaired)
        self.assertIn("(Picture 2)is ALWAYS David", repaired)
        self.assertIn("(Picture 3) is ALWAYS Goliath", repaired)

    def test_a_raw_diarizer_id_is_repaired(self):
        repaired = normalize_speaker_cues(
            "SUBJECT LOCK: <Subject 1> (SPEAKER_00) is ALWAYS Anita.",
            transcript_speakers=["SPEAKER_00"],
        )

        self.assertIn("(S1) is ALWAYS Anita", repaired)

    def test_a_picture_before_the_name_gets_no_cue_of_its_own(self):
        repaired = normalize_speaker_cues("SUBJECT LOCK: <Subject 1> (Picture 1) is ALWAYS Anita.")

        self.assertEqual(repaired, "SUBJECT LOCK: <Subject 1> (Picture 1) is ALWAYS Anita.")


class NarratorTests(unittest.TestCase):
    def test_the_decorated_lock_still_names_the_narrator(self):
        self.assertEqual(narrator_name(DECORATED), "Anita")

    def test_the_presenter_is_inside_the_film(self):
        self.assertTrue(narration_is_present(DECORATED))

    def test_the_presenter_is_stated_as_a_woman(self):
        self.assertEqual(narrator_gender(DECORATED), "woman")

        block = narration_presence(DECORATED, gender=narrator_gender(DECORATED))

        self.assertIn("Anita (a woman)", block)
        self.assertIn("never change the presenter's gender", block.lower())

    def test_the_compact_form_fits_in_a_shot(self):
        block = narration_presence(DECORATED, compact=True, gender="woman")

        self.assertIn("Anita (a woman) is ON SCREEN", block)
        self.assertLess(len(block), 400)

    def test_an_unknown_gender_is_not_invented(self):
        self.assertEqual(gender_of("young adult, athletic build"), "")
        self.assertEqual(gender_of("a woman with hazel-green eyes"), "woman")


class CastRowTests(unittest.TestCase):
    def test_every_row_of_a_one_line_brief_is_read(self):
        rows = _cast_rows(DECORATED)

        self.assertIn("Anita (S1)", rows)
        self.assertIn("David (S2)", rows)
        self.assertIn("Goliath (S3)", rows)
        self.assertIn("The Israelite Soldiers", rows)

    def test_a_sentence_is_not_a_row(self):
        rows = _section_rows(
            "Inside the royal tent, King Saul sits with his spear; the light is warm. "
            "Anita moves through the camp."
        )

        self.assertEqual(rows, [])


class FindingsTests(unittest.TestCase):
    def test_a_presence_row_is_not_an_undescribed_character(self):
        self.assertNotIn("undescribed-character", _codes(DECORATED))

    def test_an_object_being_presented_is_not_a_character(self):
        brief = DECORATED.replace(
            "CAMERA:", "CAMERA: The Tent is shown at the edge of the frame. "
        )

        self.assertNotIn("undescribed-character", _codes(brief))

    def test_a_person_the_story_acts_with_is_still_reported(self):
        brief = DECORATED.replace(
            "David prepares his sling with grey stones",
            "Abner calls out, and David prepares his sling with grey stones while Abner "
            "watches",
        )

        self.assertIn("undescribed-character", _codes(brief))

    def test_the_brief_is_not_reported_as_broken(self):
        errors = [
            finding["code"]
            for finding in _report(DECORATED)["findings"]
            if finding["severity"] == "error"
        ]

        self.assertEqual(errors, [])


class SpeakerVocabularyTests(unittest.TestCase):
    def test_the_audio_label_is_named_from_the_lock(self):
        from services.director.speaker_binding import cast_vocabulary

        vocabulary = cast_vocabulary(
            [{"speakerId": "(S1)", "name": ""}], "", brief=DECORATED,
        )

        self.assertEqual(vocabulary, {"(S1)": "Anita"})

    def test_a_cast_character_the_audio_never_speaks_as_is_not_a_speaker(self):
        from services.director.speaker_binding import cast_vocabulary

        vocabulary = cast_vocabulary(
            [{"speakerId": "(S1)", "name": ""}], "", brief=DECORATED,
        )

        self.assertNotIn("(S2)", vocabulary)
        self.assertNotIn("(S3)", vocabulary)


class StructureKeepsTheStoryTests(unittest.TestCase):
    """Pressing Structure may repair the machine half; it may not replace the film."""

    PERSON = (
        "SUBJECT LOCK:\n<Subject 1> is ALWAYS Anita.\n\n"
        "CHARACTERS:\nAnita: young woman, cream button-up shirt, navy pencil skirt.\n"
        "David: biblical figure.\n\n"
        "DESCRIPTION:\n"
        "Anita walks the audience through the ancient landscape, explaining the setting "
        "while the armies wait. David prepares his sling with grey stones; Anita walks "
        "beside him.\n\n"
        "NEGATIVE:\nNo modern clothing.\n"
    )

    MODEL = (
        "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
        "CHARACTERS:\nAnita (S1): young woman, cream button-up shirt, navy pencil skirt, "
        "black pumps.\nDavid: biblical figure.\nGoliath: giant with bronze armour.\n\n"
        "DESCRIPTION:\n"
        "An epic montage of biblical battles unfolds in slow motion, with soldiers "
        "clashing on a dusty plain while a voice describes the scale of the conflict.\n\n"
        "NEGATIVE:\nNo modern clothing.\n"
    )

    def test_the_invented_film_is_discarded(self):
        merged, replaced = merge_structured(self.PERSON, self.MODEL)

        self.assertIn("Anita walks the audience", merged)
        self.assertNotIn("montage", merged)
        self.assertIn("DESCRIPTION", replaced)

    def test_the_useful_half_of_the_answer_is_kept(self):
        merged, _replaced = merge_structured(self.PERSON, self.MODEL)

        self.assertIn("<Subject 1> (S1) is ALWAYS Anita.", merged)
        self.assertIn("Goliath", merged)

    def test_the_persons_own_row_is_not_rewritten(self):
        merged, _replaced = merge_structured(self.PERSON, self.MODEL)

        self.assertIn("Anita: young woman, cream button-up shirt, navy pencil skirt.", merged)
        self.assertNotIn("black pumps", merged)

    def test_a_faithful_rewrite_is_not_reported_as_a_replacement(self):
        # The person's own words still win -- the panel only has to explain itself when
        # the model wrote a different film.
        rewrite = self.PERSON.replace(
            "David prepares his sling with grey stones; Anita walks beside him.",
            "David prepares his sling with grey stones; Anita walks beside him, showing "
            "how he carries no armour or sword.",
        )
        merged, replaced = merge_structured(self.PERSON, rewrite)

        self.assertEqual(replaced, [])
        self.assertIn("Anita walks the audience", merged)

    def test_a_lock_about_other_people_is_refused(self):
        model = self.MODEL.replace(
            "<Subject 1> (S1) is ALWAYS Anita.", "<Subject 1> (S1) is ALWAYS Goliath.",
        )
        merged, replaced = merge_structured(self.PERSON, model)

        self.assertIn("<Subject 1> is ALWAYS Anita.", merged)
        self.assertIn("SUBJECT LOCK", replaced)


class StageListTests(unittest.TestCase):
    def test_a_stage_list_under_another_heading_is_still_read(self):
        from services.director.brief_assistant import _stage_texts

        brief = (
            "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
            "CAMERA:\nPhotorealistic palette. "
            "Stage 1: Locked-off wide shot of the valley; Anita explains the scale. "
            "Stage 2: Slow push-in on David as he prepares his sling.\n\n"
            "DESCRIPTION:\n"
            "Anita walks the audience through the ancient landscape. David prepares his "
            "sling. Anita stands near Goliath, contrasting his size with David's.\n"
        )
        stages = _stage_texts(brief)

        self.assertEqual([number for number, _body in stages], [1, 2])
        self.assertIn("wide shot of the valley", stages[0][1])
        self.assertNotIn("contrasting his size", stages[1][1])


def _report(brief: str) -> dict:
    return diagnose_brief(brief, has_audio=True, transcript_speakers=["SPEAKER_00"])


def _codes(brief: str) -> set:
    return {finding["code"] for finding in _report(brief)["findings"]}


if __name__ == "__main__":
    unittest.main(verbosity=2)
