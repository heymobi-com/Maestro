"""A two-person project must not compile a third character.

Reported on a real run: pyannote detected two speakers, yet the generated
prompts named S1..S5 and <Subject 3>/<Subject 4>. Measured against that run's own
artifact, the planner was innocent -- its raw output contained only S1 (165) and
S2 (147), and its `subjects_on_screen` rows only S1/S2. The extra cast was
created after the LLM, by three separate defects:

1. `_build_stable_speaker_registry` minted a fresh number for every unseen
   `speaker_id`, and beats name their speaker by character id
   ("director_identity_valeria") as well as by label. Each participant therefore
   got a second name: (S3) and (S4) for the same two people. 40 recompiled clips
   carried S3 x41 and S4 x27.
2. A `subjects_on_screen` row packed two people behind one label --
   "Valeria (S1), brunette, navy blazer / Ricardo (S2), mature man in grey
   sweater & glasses." -- which produced a duplicate entry for the first speaker
   plus a fresh <Subject 3> to bind the second person's picture.
3. Two rows describing the SAME person ("Valeria (S1), brunette, navy blazer"
   and "Valeria (S1)") became two subject slots, so the spare one was emitted as
   `<Subject 3> (S1): Valeria`.

After the fixes the same 40 clips compile with S1 and S2, and <Subject 1> /
<Subject 2>, only.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_dialogue import (  # noqa: E402
    _build_stable_speaker_registry,
    _merge_subject_rows_for_the_same_speaker,
    _split_multi_speaker_subject_rows,
    compile_h3_clip_plans,
)

VALERIA = {
    "visual_description": "Valeria (S1), a young woman with brown hair in a navy blazer.",
    "character_id": "director_identity_valeria",
    "speaker_name": "Valeria",
}
RICARDO = {
    "visual_description": "Ricardo (S2), an older man with grey hair and glasses.",
    "character_id": "director_identity_ricardo",
    "speaker_name": "Ricardo",
}


def _plan(subjects, beats, prompt="[Shot 1] Two colleagues talk."):
    return {
        "video_prompt": prompt,
        "image_prompt": "",
        "_director_h3_source_prompt": prompt,
        "_director_h3_compiled_prompt": "",
        "_director_dialogue_beats": beats,
        "_director_subjects_on_screen": subjects,
        "_director_h3_prompt_mode": "ref2va",
        "_director_h3_model_family": "ref2va",
        "_director_duration_sec": 8.0,
    }


class SpeakerRegistryTests(unittest.TestCase):
    def test_an_identity_id_keeps_the_label_that_person_already_has(self):
        plan = _plan(
            [VALERIA, RICARDO],
            [
                {"speaker_id": "(S1)", "spoken_text": "Primero."},
                {"speaker_id": "director_identity_valeria", "spoken_text": "Segundo."},
                {"speaker_id": "director_identity_ricardo", "spoken_text": "Tercero."},
            ],
        )

        registry = _build_stable_speaker_registry([plan])

        self.assertEqual(registry["director_identity_valeria"]["stable_id"], "(S1)")
        self.assertEqual(registry["director_identity_ricardo"]["stable_id"], "(S2)")

    def test_a_genuinely_new_speaker_still_gets_a_number(self):
        # The guard must not swallow a real third participant.
        plan = _plan(
            [VALERIA, RICARDO],
            [
                {"speaker_id": "(S1)", "spoken_text": "Primero."},
                {"speaker_id": "(S2)", "spoken_text": "Segundo."},
                {"speaker_id": "(S3)", "spoken_text": "Tercero."},
            ],
        )

        registry = _build_stable_speaker_registry([plan])

        self.assertEqual(registry["(s3)"]["stable_id"], "(S3)")

    def test_the_registry_does_not_hand_out_a_second_label_per_identity(self):
        plan = _plan(
            [VALERIA, RICARDO],
            [
                {"speaker_id": "director_identity_valeria", "spoken_text": "Uno."},
                {"speaker_id": "director_identity_ricardo", "spoken_text": "Dos."},
            ],
        )

        numbers = {
            entry["stable_id"]
            for entry in _build_stable_speaker_registry([plan]).values()
        }

        self.assertEqual(numbers, {"(S1)", "(S2)"})


class SubjectRowTests(unittest.TestCase):
    def test_a_row_packing_two_people_is_split(self):
        plan = _plan([{
            "visual_description": (
                "Valeria (S1), brunette, navy blazer / Ricardo (S2), "
                "mature man in grey sweater & glasses."
            ),
            "position_or_relation": "split frame focus",
        }], [])

        _split_multi_speaker_subject_rows([plan])

        rows = plan["_director_subjects_on_screen"]
        self.assertEqual(len(rows), 2)
        self.assertIn("Valeria (S1)", rows[0]["visual_description"])
        self.assertIn("Ricardo (S2)", rows[1]["visual_description"])
        # The second person's name must not be left trailing in the first row.
        self.assertNotIn("Ricardo", rows[0]["visual_description"])

    def test_a_single_person_row_is_untouched(self):
        row = {"visual_description": "Valeria (S1), looking troubled,"}
        plan = _plan([row], [])

        _split_multi_speaker_subject_rows([plan])

        self.assertEqual(plan["_director_subjects_on_screen"], [row])

    def test_two_rows_for_one_person_are_merged(self):
        plan = _plan([
            {"visual_description": "Valeria (S1), brunette, navy blazer."},
            {"visual_description": "Valeria (S1)"},
            {"visual_description": "Ricardo (S2), grey sweater."},
        ], [])

        _merge_subject_rows_for_the_same_speaker([plan])

        rows = plan["_director_subjects_on_screen"]
        self.assertEqual(len(rows), 2)
        self.assertIn("navy blazer", rows[0]["visual_description"])
        self.assertIn("Ricardo", rows[1]["visual_description"])

    def test_the_merge_keeps_the_fuller_description(self):
        plan = _plan([
            {"visual_description": "Valeria (S1)"},
            {"visual_description": "Valeria (S1), brunette, navy blazer."},
        ], [])

        _merge_subject_rows_for_the_same_speaker([plan])

        self.assertEqual(len(plan["_director_subjects_on_screen"]), 1)
        self.assertIn("navy blazer", plan["_director_subjects_on_screen"][0]["visual_description"])

    def test_rows_without_a_label_are_left_alone(self):
        rows = [{"visual_description": "A crowd in the background."}]
        plan = _plan(rows, [])

        _split_multi_speaker_subject_rows([plan])
        _merge_subject_rows_for_the_same_speaker([plan])

        self.assertEqual(plan["_director_subjects_on_screen"], rows)


class CompiledCastTests(unittest.TestCase):
    def test_a_two_person_shot_compiles_exactly_two_subjects(self):
        plan = _plan(
            [VALERIA, RICARDO],
            [
                {"speaker_id": "(S1)", "spoken_text": "Hablemos del proyecto."},
                {"speaker_id": "director_identity_ricardo", "spoken_text": "De acuerdo."},
            ],
        )

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])
        out = str(plan["video_prompt"])

        # findall returns the capture group, so these are the bare labels.
        self.assertEqual(sorted(set(re.findall(r"\((S\d+)\)", out))), ["S1", "S2"])
        self.assertEqual(sorted(set(re.findall(r"<Subject\s+\d+>", out))), ["<Subject 1>", "<Subject 2>"])

    def test_the_packed_and_duplicated_rows_still_compile_two_subjects(self):
        plan = _plan(
            [
                {
                    "visual_description": (
                        "Valeria (S1), brunette, navy blazer / Ricardo (S2), "
                        "mature man in grey sweater & glasses."
                    ),
                },
                {"visual_description": "Valeria (S1)"},
            ],
            [{"speaker_id": "(S1)", "spoken_text": "Hablemos."}],
        )

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])
        out = str(plan["video_prompt"])

        self.assertEqual(sorted(set(re.findall(r"<Subject\s+\d+>", out))), ["<Subject 1>", "<Subject 2>"])
        self.assertNotIn("<Subject 3>", out)


# The shape a real two-person run produced: the planner labelled each
# participant both by name and by the label itself, and beats named their
# speaker with the bare id form as well.
LABEL_NAMED_VALERIA = {
    "visual_description": "Valeria looking at the void of thought.",
    "character_id": "director_identity_s1",
    "speaker_name": "(S1)",
}
LABEL_NAMED_RICARDO = {
    "visual_description": "Ricardo listening in silence.",
    "character_id": "director_identity_s2",
    "speaker_name": "(S2)",
}


class SpeakerKeysThatAlreadySpellOutALabelTests(unittest.TestCase):
    """A key that names an existing label is that participant, not a new one.

    Measured on a real run: `_build_stable_speaker_registry` minted a number for
    the keys `(s1)`, `(s2)` and `director_identity_s2` because the subject rows
    state their labels in `speaker_name` rather than in their prose. Two people
    therefore compiled as S1..S4, and 32 dialogue beats -- in 12 of 177 clips --
    were spoken by an `(S4)` that no subject row described.
    """

    def _registry(self, beats):
        plan = _plan(
            [LABEL_NAMED_VALERIA, LABEL_NAMED_RICARDO],
            beats,
        )
        return _build_stable_speaker_registry([plan])

    def test_a_label_key_binds_to_its_own_label(self):
        registry = self._registry([
            {"speaker_id": "(s1)", "spoken_text": "Hola."},
            {"speaker_id": "(s2)", "spoken_text": "Hola."},
        ])

        self.assertEqual(registry["(s1)"]["stable_id"], "(S1)")
        self.assertEqual(registry["(s2)"]["stable_id"], "(S2)")

    def test_an_identity_id_built_from_a_label_binds_to_that_label(self):
        registry = self._registry([
            {"speaker_id": "director_identity_s1", "spoken_text": "Hola."},
            {"speaker_id": "director_identity_s2", "spoken_text": "Hola."},
        ])

        self.assertEqual(registry["director_identity_s1"]["stable_id"], "(S1)")
        self.assertEqual(registry["director_identity_s2"]["stable_id"], "(S2)")

    def test_two_people_never_compile_a_third_or_fourth_label(self):
        plan = _plan(
            [LABEL_NAMED_VALERIA, LABEL_NAMED_RICARDO],
            [
                {"speaker_id": "director_identity_s1", "spoken_text": "Hola."},
                {"speaker_id": "director_identity_s2", "spoken_text": "Hola."},
            ],
        )

        compile_h3_clip_plans([plan], prompt_modes=["ref2va"], durations=[8.0])
        registry = plan["_director_speaker_registry"]
        labels = sorted({entry["stable_id"] for entry in registry.values()})

        self.assertEqual(labels, ["(S1)", "(S2)"])
        out = str(plan["video_prompt"])
        self.assertNotIn("(S3)", out)
        self.assertNotIn("(S4)", out)


if __name__ == "__main__":
    unittest.main()
