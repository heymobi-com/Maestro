"""The appearance comes from the reference picture, not from the brief.

Measured on a real project: the brief said "David: presence in the biblical action
stages" and the narrator was "una mujer joven, bella y carismatica" while her reference
picture showed a woman with her hair tied back, a white shirt, a blue skirt and low heels.
The picture was attached to every clip and the text never agreed with it, so each shot
re-invented the face. These tests hold the read-from-the-image path that replaces that.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from services.director.cast_vision import (  # noqa: E402
    CAST_LOOK_PROMPT,
    LOOK_IMAGE_MAX_SIZE,
    LOOK_LIMIT,
    appearance_from_reference,
    apply_cast_descriptions,
    apply_reference_roster,
    canonical_speaker_label,
    canonical_speaker_labels,
    cast_descriptions,
    looks_block,
    match_name,
    reference_roster,
    references_for_look,
    roster_block,
    same_name,
    speaker_cue_for,
)
from services.director.brief_assistant import (  # noqa: E402
    consolidate_cast_rows,
    normalized_brief,
    normalize_speaker_cues,
)

ANITA_LOOK = (
    "early 30s; slim; fair skin; brunette hair tied back in a low bun; brown eyes; "
    "white fitted shirt; mid-length blue skirt; low nude heels; small gold earrings"
)


def _fake_generate(prompt, system_prompt="", max_new_tokens=0, temperature=0.0,
                   image_paths=None, **kwargs):
    return ANITA_LOOK


class ReferenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.image = os.path.join(self.tmp, "anita.png")
        with open(self.image, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")

    def _ref(self, **overrides):
        reference = {
            "type": "image",
            "path": self.image,
            "filename": "anita.png",
            "role": "Anita (S1)",
            "image_intent": "identity",
        }
        reference.update(overrides)
        return reference

    def test_an_identity_picture_is_read(self):
        self.assertEqual(len(references_for_look([self._ref()])), 1)

    def test_a_scene_reference_is_not_a_character_sheet(self):
        self.assertEqual(references_for_look([self._ref(image_intent="scene")]), [])

    def test_one_picture_per_name(self):
        refs = [self._ref(), self._ref(filename="anita_copy.png")]

        self.assertEqual(len(references_for_look(refs)), 1)

    def test_a_missing_file_is_skipped(self):
        self.assertEqual(references_for_look([self._ref(path="nope.png")]), [])

    def test_the_subject_cue_is_not_part_of_the_name(self):
        looks = cast_descriptions([self._ref()], generate=_fake_generate)

        self.assertEqual(list(looks), ["Anita"])

    def test_the_picture_is_what_is_sent_to_the_model(self):
        sent = {}

        def capture(prompt, image_paths=None, **kwargs):
            sent["paths"] = list(image_paths or [])
            sent["prompt"] = prompt
            sent.update(kwargs)
            return ANITA_LOOK

        appearance_from_reference(self.image, generate=capture)

        self.assertEqual(sent["paths"], [self.image])
        self.assertEqual(sent["prompt"], CAST_LOOK_PROMPT)

    def test_a_2k_reference_is_sent_at_full_detail(self):
        sent = {}

        def capture(prompt, image_paths=None, **kwargs):
            sent.update(kwargs)
            return ANITA_LOOK

        appearance_from_reference(self.image, generate=capture)

        self.assertEqual(sent["image_max_size"], LOOK_IMAGE_MAX_SIZE)
        self.assertGreaterEqual(LOOK_IMAGE_MAX_SIZE, 1280)
        self.assertGreaterEqual(sent["max_new_tokens"], 512)

    def test_the_prompt_asks_for_every_visible_detail(self):
        for expected in ("bottle green", "eyebrow", "parting", "neckline", "heel height"):
            self.assertIn(expected, CAST_LOOK_PROMPT, msg=expected)
        for refused in ("background", "pose", "lighting", "camera"):
            self.assertIn(refused, CAST_LOOK_PROMPT, msg=refused)


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.image = os.path.join(self.tmp, "anita.png")
        with open(self.image, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")

    def test_the_answer_is_the_appearance(self):
        self.assertEqual(
            appearance_from_reference(self.image, generate=_fake_generate),
            ANITA_LOOK,
        )

    def test_a_label_the_model_added_is_stripped(self):
        look = appearance_from_reference(
            self.image,
            generate=lambda *a, **k: 'Character sheet: "fair skin, brunette hair tied back"',
        )

        self.assertEqual(look, "fair skin, brunette hair tied back")

    def test_a_refusal_is_not_an_appearance(self):
        for answer in ("I cannot describe this image.", "This image shows a beach.", ""):
            with self.subTest(answer=answer):
                look = appearance_from_reference(self.image, generate=lambda *a, **k: answer)
                self.assertEqual(look, "")

    def test_a_row_that_says_when_someone_appears_is_refused(self):
        look = appearance_from_reference(
            self.image,
            generate=lambda *a, **k: "David: presence in the biblical action stages.",
        )

        self.assertEqual(look, "")

    def test_a_raise_leaves_the_brief_alone(self):
        def boom(*args, **kwargs):
            raise RuntimeError("no model")

        self.assertEqual(appearance_from_reference(self.image, generate=boom), "")
        self.assertEqual(cast_descriptions([{"path": self.image, "role": "Anita"}], generate=boom), {})

    def test_no_vision_model_leaves_the_brief_alone(self):
        self.assertEqual(appearance_from_reference(self.image, generate=None), "")

    def test_the_look_is_capped(self):
        calls = []

        def counting(*args, **kwargs):
            calls.append(1)
            return ANITA_LOOK

        refs = [
            {"path": self.image, "role": f"Person{index}", "type": "image"}
            for index in range(9)
        ]
        cast_descriptions(refs, generate=counting, limit=2)

        self.assertEqual(len(calls), 2)


class LooksBlockTests(unittest.TestCase):
    def test_only_the_named_characters_are_written(self):
        block = looks_block(
            {"Anita": "white shirt, blue skirt", "David": "undyed tunic"},
            ["Anita"],
        )

        self.assertIn("Anita: white shirt", block)
        self.assertNotIn("David", block)

    def test_a_name_matches_regardless_of_case(self):
        block = looks_block({"Anita": "white shirt"}, ["anita"])

        self.assertIn("Anita: white shirt", block)

    def test_nothing_asked_for_is_nothing_written(self):
        self.assertEqual(looks_block({"Anita": "white shirt"}, ["Goliat"]), "")
        self.assertEqual(looks_block({}, ["Anita"]), "")

    def test_a_detailed_read_of_several_characters_is_bounded(self):
        looks = {
            "Anita": "white shirt, blue skirt, " * 30,
            "David": "undyed tunic, " * 30,
            "Goliat": "bronze armour, " * 30,
        }

        block = looks_block(looks, ["Anita", "David", "Goliat"], limit=600)

        self.assertLessEqual(len(block), 620)
        self.assertIn("Anita", block)


class ApplyTests(unittest.TestCase):
    def test_a_presence_row_is_replaced_by_the_picture(self):
        brief = (
            "CHARACTERS:\nAnita: narrator.\n\n"
            "DESCRIPTION:\nStage 1: Anita walks into the valley.\n"
        )

        out = apply_cast_descriptions(brief, {"Anita": ANITA_LOOK})

        self.assertIn(f"Anita: {ANITA_LOOK}", out)
        self.assertNotIn("Anita: narrator.", out)

    def test_the_writers_own_description_wins(self):
        brief = "CHARACTERS:\nAnita: woman in a cream linen shirt and gold hoops.\n"

        out = apply_cast_descriptions(brief, {"Anita": ANITA_LOOK})

        self.assertIn("cream linen shirt", out)
        self.assertNotIn(ANITA_LOOK, out)

    def test_a_missing_row_is_added(self):
        brief = "CHARACTERS:\nDavid: young shepherd in an undyed tunic.\n"

        out = apply_cast_descriptions(brief, {"Anita": ANITA_LOOK})

        self.assertIn(f"Anita: {ANITA_LOOK}", out)
        self.assertIn("David: young shepherd", out)

    def test_a_brief_without_a_characters_section_gains_one(self):
        brief = "DESCRIPTION:\nStage 1: Anita walks into the valley.\n"

        out = apply_cast_descriptions(brief, {"Anita": ANITA_LOOK})

        self.assertIn("CHARACTERS:", out)
        self.assertIn(f"Anita: {ANITA_LOOK}", out)
        self.assertIn("DESCRIPTION:", out)

    def test_the_sections_after_characters_survive(self):
        brief = (
            "CHARACTERS:\nAnita: narrator.\n\n"
            "CAMERA:\n35mm anamorphic.\n\n"
            "DESCRIPTION:\nStage 1: Anita walks into the valley.\n"
        )

        out = apply_cast_descriptions(brief, {"Anita": ANITA_LOOK})

        self.assertIn("CAMERA:", out)
        self.assertIn("35mm anamorphic.", out)
        self.assertIn("Stage 1: Anita walks into the valley.", out)

    def test_a_long_read_is_kept_up_to_the_limit(self):
        long_look = "detail " * 400
        tmp = tempfile.mkdtemp()
        image = os.path.join(tmp, "anita.png")
        with open(image, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")

        look = appearance_from_reference(image, generate=lambda *a, **k: long_look)

        self.assertEqual(len(look), LOOK_LIMIT)

    def test_no_look_is_no_change(self):
        brief = "CHARACTERS:\nAnita: narrator.\n"

        self.assertEqual(apply_cast_descriptions(brief, {}), brief)


class AnitaNarrativeTests(unittest.TestCase):
    """The documentary that exposed this: one narrator moving through the story."""

    PROSE = (
        "This documentary contains only one narrator and one closed cast of characters. "
        "The narrator is Anita, a young, beautiful, and charismatic woman, and Anita is "
        "the only person who speaks in the entire documentary. Anita narrates the "
        "biblical story of David and Goliath in a photorealistic documentary in which "
        "Anita moves immersively through the settings of the story. Anita always stands "
        "at the center of the cinematic action, showing and explaining what is happening "
        "with the characters, the wars, and the spaces of the ancient biblical world. "
        "The documentary presents epic biblical scenes that visually illustrate what is "
        "being narrated, so that the action unfolds in sync with the spoken account of "
        "Anita. The only characters who ever appear are Anita and the biblical figures "
        "belonging to the story of David and Goliath: David, Goliath, King Saul, the "
        "Israelite soldiers, the Philistine soldiers, and the people of the ancient "
        "biblical world. No other character is created, added, or invented at any "
        "moment. The biblical figures appear around Anita as if the action were "
        "happening at that very moment."
    )

    def test_a_format_brief_is_not_a_shot_list(self):
        from services.director.brief_assistant import description_states_events

        self.assertFalse(description_states_events(self.PROSE))

    def test_a_format_brief_gets_no_stages(self):
        from services.director.planners.short_film import _brief_stage_list

        self.assertEqual(_brief_stage_list(self.PROSE), [])

    def test_a_narrated_documentary_is_told_the_narration_carries_the_beats(self):
        from services.director.brief_assistant import diagnose_brief

        codes = {
            finding["code"]
            for finding in diagnose_brief(
                self.PROSE, kind="short_film_audio", clip_count=42,
                has_audio=True, transcript_speakers=["S1"],
            )["findings"]
        }
        self.assertIn("narration-driven", codes)
        self.assertNotIn("abstract-beats", codes)

    def test_without_audio_the_same_prose_is_a_warning(self):
        from services.director.brief_assistant import diagnose_brief

        codes = {
            finding["code"]
            for finding in diagnose_brief(
                self.PROSE, kind="short_film_story", clip_count=42, has_audio=False,
            )["findings"]
        }
        self.assertIn("abstract-beats", codes)

    def test_the_structure_prompt_forbids_inventing_stages(self):
        from services.director.brief_assistant import structure_prompt

        system, _user = structure_prompt(
            self.PROSE, kind="short_film_audio", clip_count=42,
            transcript_speakers=["S1"], has_audio=True,
        )

        self.assertIn("Do NOT write Stage 1", system)
        self.assertIn("Never relocate the narrator", system)
        self.assertIn("STANDING", system)

    def test_a_story_brief_is_still_given_stages(self):
        from services.director.brief_assistant import structure_prompt

        story = (
            "DESCRIPTION:\n"
            "Stage 1 (kitchen): Abuela Rosa lifts the cup from the wooden table.\n"
            "Stage 2 (street): Abuela Rosa crosses the cobbled street and turns.\n"
        )
        system, _user = structure_prompt(
            story, kind="short_film_audio", clip_count=20,
            transcript_speakers=["S1"], has_audio=True,
        )

        self.assertNotIn("Do NOT write Stage 1", system)
        self.assertIn('"Stage N (label): ..."', system)


class VisionCallIsOptionalTests(unittest.TestCase):
    def test_a_pipeline_with_no_references_reads_nothing(self):
        from services.director_pipeline import _h3_cast_looks

        params = {"minimax_h3_references": []}

        with mock.patch("services.director.cast_vision.cast_descriptions", return_value={}):
            self.assertEqual(_h3_cast_looks(params), {})
        self.assertNotIn("_director_cast_looks", params)

    def test_a_read_look_is_cached_on_the_params(self):
        from services.director_pipeline import _h3_cast_looks

        params = {"minimax_h3_references": [{"path": "x.png"}]}
        with mock.patch(
            "services.director.cast_vision.cast_descriptions",
            return_value={"Anita": ANITA_LOOK},
        ) as described:
            first = _h3_cast_looks(params)
            second = _h3_cast_looks(params)

        self.assertEqual(first, {"Anita": ANITA_LOOK})
        self.assertEqual(second, first)
        self.assertEqual(described.call_count, 1)


class NameMatchingTests(unittest.TestCase):
    """A picture and a brief spell the same person differently often enough to matter."""

    def test_one_letter_apart_is_the_same_person(self):
        self.assertTrue(same_name("Goliat", "Goliath"))

    def test_case_and_padding_do_not_matter(self):
        self.assertTrue(same_name("  anita ", "Anita"))
        self.assertTrue(same_name("Anita (S1)", "Anita"))

    def test_an_honorific_may_be_dropped(self):
        self.assertTrue(same_name("Abuela Rosa", "Rosa"))

    def test_different_people_never_match(self):
        self.assertFalse(same_name("David", "Saul"))
        self.assertFalse(same_name("Anita", "Goliat"))

    def test_a_short_name_is_not_matched_loosely(self):
        self.assertFalse(same_name("Ana", "Anita"))


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for name in ("anita", "david", "goliat"):
            path = os.path.join(self.tmp, name + ".png")
            with open(path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n")
            self.paths[name] = path

    def _refs(self):
        return [
            {"type": "image", "path": self.paths["anita"], "role": "Anita (S1)",
             "image_intent": "identity"},
            {"type": "image", "path": self.paths["david"], "role": "David",
             "image_intent": "identity"},
            {"type": "image", "path": self.paths["goliat"], "role": "Goliat",
             "image_intent": "identity"},
        ]

    def test_the_roster_keeps_manifest_order_and_the_cue(self):
        roster = reference_roster(self._refs())

        self.assertEqual([item["number"] for item in roster], [1, 2, 3])
        self.assertEqual([item["name"] for item in roster], ["Anita", "David", "Goliat"])
        self.assertEqual(roster[0]["cue"], "S1")
        self.assertEqual(roster[1]["cue"], "")

    def test_the_roster_carries_the_appearance_when_one_was_read(self):
        roster = reference_roster(self._refs(), {"Anita": "white shirt, blue skirt"})

        self.assertEqual(roster[0]["look"], "white shirt, blue skirt")

    def test_the_block_names_every_picture_and_forbids_invention(self):
        block = roster_block(reference_roster(self._refs()))

        self.assertIn("REFERENCES:", block)
        self.assertIn("Anita (S1)", block)
        self.assertIn("2. David", block)
        self.assertIn("Never invent their face", block)


class RosterApplyTests(unittest.TestCase):
    BRIEF = (
        "SUBJECT LOCK:\n"
        "<Subject 1> is ALWAYS Anita.\n"
        "The biblical figures (David, Goliath) never become a Subject.\n\n"
        "CHARACTERS:\n"
        "Anita: young, beautiful and charismatic woman.\n\n"
        "DESCRIPTION:\n"
        "STANDING RULE: Anita is in every shot.\n"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.refs = []
        for name, role in (("anita", "Anita (S1)"), ("david", "David"), ("goliat", "Goliat")):
            path = os.path.join(self.tmp, name + ".png")
            with open(path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n")
            self.refs.append(
                {"type": "image", "path": path, "role": role, "image_intent": "identity"}
            )

    def test_the_cue_reaches_the_lock_and_the_cast_row(self):
        out = apply_reference_roster(self.BRIEF, self.refs)

        self.assertIn("<Subject 1> (S1) is ALWAYS Anita.", out)
        self.assertIn("Anita (S1): young, beautiful", out)

    def test_the_section_is_appended_once(self):
        once = apply_reference_roster(self.BRIEF, self.refs)
        twice = apply_reference_roster(once, self.refs)

        self.assertEqual(once.count("REFERENCES:"), 1)
        self.assertEqual(twice.count("REFERENCES:"), 1)
        self.assertNotIn("(S1) (S1)", twice)

    def test_the_brief_spelling_wins_over_the_manifest(self):
        brief = self.BRIEF.replace("Goliath", "Goliath").replace(
            "CHARACTERS:", "CHARACTERS:\nGoliath: bronze scale armour."
        )

        out = apply_reference_roster(brief, self.refs)

        self.assertIn("3. Goliath —", out)
        self.assertNotIn("3. Goliat —", out)

    def test_a_picture_with_no_cue_does_not_touch_the_lock(self):
        refs = [self.refs[1]]
        out = apply_reference_roster(self.BRIEF, refs)

        self.assertIn("<Subject 1> is ALWAYS Anita.", out)
        self.assertIn("REFERENCES:", out)

    def test_no_references_is_no_change(self):
        self.assertEqual(apply_reference_roster(self.BRIEF, []), self.BRIEF)


class ConsolidationTests(unittest.TestCase):
    def test_a_duplicate_row_becomes_one_row_keeping_both_facts(self):
        brief = (
            "CHARACTERS:\nAnita: white shirt.\nDavid: undyed tunic.\n\n"
            "PRESENCES:\nDavid: does not speak.\nKing Saul: does not speak.\n"
        )

        out = consolidate_cast_rows(brief)

        self.assertEqual(out.count("David:"), 1)
        self.assertIn("undyed tunic", out)
        self.assertIn("does not speak", out)
        self.assertIn("King Saul: does not speak.", out)

    def test_two_spellings_of_one_person_merge(self):
        brief = (
            "CHARACTERS:\nGoliat: bronze armour.\n\n"
            "PRESENCES:\nGoliath: does not speak.\n"
        )

        out = consolidate_cast_rows(brief)

        self.assertEqual(out.count("bronze armour"), 1)
        self.assertNotIn("Goliath: does not speak.", out)

    def test_a_label_inside_the_description_is_not_a_section(self):
        brief = (
            "CHARACTERS:\nAnita: white shirt.\n\n"
            "DESCRIPTION:\nSTANDING RULE: Anita is in every shot.\n\n"
            "NEGATIVE:\nNo invented person.\n"
        )

        out = consolidate_cast_rows(brief)

        self.assertIn("DESCRIPTION:\nSTANDING RULE: Anita is in every shot.", out)
        self.assertNotIn("STANDING RULE:\nAnita", out)

    def test_a_brief_without_presences_is_unchanged(self):
        brief = "CHARACTERS:\nAnita: white shirt.\n"

        self.assertEqual(consolidate_cast_rows(brief), brief)


class CueBindingTests(unittest.TestCase):
    """The lock must carry the audio's speaker label, and the compiler must read it."""

    def test_the_lock_parses_with_the_cue(self):
        from services.director.h3_dialogue import (
            project_subject_lock,
            project_subject_lock_cues,
        )

        text = "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n"

        self.assertEqual(project_subject_lock(text), {1: "Anita"})
        self.assertEqual(project_subject_lock_cues(text), {1: "S1"})

    def test_a_lock_without_a_cue_still_names_the_participant(self):
        from services.director.h3_dialogue import project_subject_lock

        self.assertEqual(project_subject_lock("<Subject 2> is ALWAYS Figura."), {2: "Figura"})

    def test_the_shot_context_repeats_the_cue(self):
        from services.director_pipeline import _h3_shot_project_context

        brief = "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\nThe story.\n"

        self.assertIn("<Subject 1> (S1) is ALWAYS Anita.", _h3_shot_project_context(brief, 0, 8))

    def test_a_missing_cue_is_reported_when_the_audio_has_speakers(self):
        from services.director.brief_assistant import diagnose_brief

        brief = (
            "SUBJECT LOCK:\n<Subject 1> is ALWAYS Anita.\n\n"
            "DESCRIPTION:\nSTANDING RULE: Anita is in every shot.\n"
        )
        codes = {
            finding["code"]
            for finding in diagnose_brief(
                brief, kind="short_film_audio", has_audio=True,
                transcript_speakers=["S1"],
            )["findings"]
        }

        self.assertIn("lock-missing-cue", codes)

    def test_a_present_cue_is_not_reported(self):
        from services.director.brief_assistant import diagnose_brief

        brief = (
            "SUBJECT LOCK:\n<Subject 1> (S1) is ALWAYS Anita.\n\n"
            "DESCRIPTION:\nSTANDING RULE: Anita is in every shot.\n"
        )
        codes = {
            finding["code"]
            for finding in diagnose_brief(
                brief, kind="short_film_audio", has_audio=True,
                transcript_speakers=["S1"],
            )["findings"]
        }

        self.assertNotIn("lock-missing-cue", codes)


class StandbyLabelTests(unittest.TestCase):
    def test_a_standing_rule_is_not_an_action(self):
        from services.director.scene_policy import beat_states_an_event

        self.assertFalse(
            beat_states_an_event(
                "STANDING RULE: Anita is at the center of the cinematic action in every shot."
            )
        )

    def test_a_performed_action_still_counts(self):
        from services.director.scene_policy import beat_states_an_event

        self.assertTrue(beat_states_an_event("Kitchen: Rosa lifts the cup."))


class SpeakerCueTests(unittest.TestCase):
    """The transcript says SPEAKER_00; every label the compiler reads is (S1)."""

    def test_a_raw_diarization_id_becomes_the_first_label(self):
        self.assertEqual(canonical_speaker_label("SPEAKER_00"), "S1")
        self.assertEqual(canonical_speaker_label("speaker_01"), "S2")

    def test_a_canonical_label_is_left_alone(self):
        self.assertEqual(canonical_speaker_label("(S2)"), "S2")
        self.assertEqual(canonical_speaker_label("s3"), "S3")

    def test_a_name_is_not_a_cue(self):
        self.assertEqual(canonical_speaker_label("Anita"), "")

    def test_the_labels_follow_first_appearance_not_the_suffix(self):
        self.assertEqual(canonical_speaker_labels(["SPEAKER_03"]), ["S1"])
        self.assertEqual(
            canonical_speaker_labels(["SPEAKER_00", "SPEAKER_01"]), ["S1", "S2"]
        )

    def test_a_mixed_list_does_not_reuse_a_number(self):
        labels = canonical_speaker_labels(["S1", "SPEAKER_00"])

        self.assertEqual(labels[0], "S1")
        self.assertNotEqual(labels[1], "S1")

    def test_the_model_answer_is_repaired(self):
        brief = (
            "SUBJECT LOCK:\n"
            "<Subject 1> (SPEAKER_00) is ALWAYS Anita.\n"
            "<Subject 2> (speaker_1) is ALWAYS Ricardo.\n"
        )

        out = normalize_speaker_cues(brief, ["SPEAKER_00", "SPEAKER_01"])

        self.assertIn("<Subject 1> (S1) is ALWAYS Anita.", out)
        self.assertIn("<Subject 2> (S2) is ALWAYS Ricardo.", out)

    def test_a_brief_with_a_raw_cue_still_parses(self):
        from services.director.h3_dialogue import (
            project_subject_lock,
            project_subject_lock_cues,
        )

        text = "SUBJECT LOCK:\n<Subject 1> (SPEAKER_00) is ALWAYS Anita.\n"

        self.assertEqual(project_subject_lock(text), {1: "Anita"})
        self.assertEqual(project_subject_lock_cues(text), {1: "S1"})


class DeriveLockTests(unittest.TestCase):
    """The mechanical pass must not leave a prose brief without a lock."""

    PROSE = (
        "This documentary contains only one narrator. The narrator is Anita, and Anita "
        "is the only person who speaks. Anita narrates the biblical story of David and "
        "Goliath. The only characters who ever appear are Anita and David, Goliath."
    )

    def test_one_voice_and_one_narrator_gives_a_lock(self):
        out = normalized_brief(
            self.PROSE, kind="short_film_audio", transcript_speakers=["SPEAKER_00"]
        )

        self.assertIn("<Subject 1> (S1) is ALWAYS Anita.", out)

    def test_the_narration_mentioning_another_name_does_not_win(self):
        # "narrates the story of David" put David near a speaking verb.
        out = normalized_brief(
            self.PROSE, kind="short_film_audio", transcript_speakers=["SPEAKER_00"]
        )

        self.assertNotIn("ALWAYS David", out)

    def test_two_voices_is_not_unambiguous(self):
        out = normalized_brief(
            self.PROSE, kind="short_film_audio",
            transcript_speakers=["SPEAKER_00", "SPEAKER_01"],
        )

        self.assertNotIn("SUBJECT LOCK", out)

    def test_no_audio_means_no_lock(self):
        self.assertNotIn(
            "SUBJECT LOCK", normalized_brief(self.PROSE, kind="short_film_audio")
        )

    def test_an_existing_lock_is_never_rewritten(self):
        brief = "SUBJECT LOCK:\n<Subject 1> (S2) is ALWAYS Ricardo.\n"

        out = normalized_brief(
            brief, kind="short_film_audio", transcript_speakers=["SPEAKER_00"]
        )

        self.assertIn("ALWAYS Ricardo", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
