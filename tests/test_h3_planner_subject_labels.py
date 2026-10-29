"""The compiler writes the <Subject N> labels; a planner copy is an undefined subject.

Measured on a real plan that failed with "Shot 11: Ref2VA visual action uses undefined
subjects: 7": the writer labelled the black door `<Subject 7> (Sx)` in the shot's
`ending_beat` and in one dialogue beat's `physical_cue`, while that shot's own cast rows
defined only Subjects 1 and 2. Only visible descriptions were being cleaned, so every
other authored field carried the tag straight into the action text.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.director.h3_dialogue import _strip_planner_subject_labels  # noqa: E402


def _plan(**overrides) -> dict:
    plan = {
        "video_prompt": "Locked-off shot down a corridor.",
        "_director_h3_source_prompt": "[Shot 1] Locked-off shot down a corridor.",
        "spatial_setup": "The door sits at the far end.",
        "environment": "A narrow stone corridor.",
        "ending_beat": (
            "<Subject 7> (Sx) La puerta negra remains shut as the fog pools at its base."
        ),
        "action_beats": ["<Subject 7> (Sx) La puerta negra stands open."],
        "subjects_on_screen": [
            {"visual_description": "<Subject 7> (Sx) La puerta negra, narrow black door."},
        ],
        "_director_dialogue_beats": [
            {"spoken_text": "No.", "physical_cue": "<Subject 1> (S1) Rosa steps back."},
        ],
        "_director_audio_plan": {"mode": "dialogue_driven"},
    }
    plan.update(overrides)
    return plan


class StripPlannerLabelsTests(unittest.TestCase):
    def test_the_undefined_label_leaves_every_authored_field(self):
        plan = _plan()

        _strip_planner_subject_labels(plan)

        self.assertNotIn("Subject 7", plan["ending_beat"])
        self.assertNotIn("Subject 7", plan["action_beats"][0])
        self.assertNotIn("Subject 7", plan["subjects_on_screen"][0]["visual_description"])

    def test_the_content_survives(self):
        plan = _plan()

        _strip_planner_subject_labels(plan)

        self.assertIn("La puerta negra remains shut", plan["ending_beat"])
        self.assertIn("narrow black door", plan["subjects_on_screen"][0]["visual_description"])

    def test_a_real_speaker_cue_is_kept(self):
        # The compiler recovers a row's participant from this cue; it is not junk.
        plan = _plan()

        _strip_planner_subject_labels(plan)

        cue = plan["_director_dialogue_beats"][0]["physical_cue"]
        self.assertIn("(S1)", cue)
        self.assertIn("Rosa steps back", cue)

    def test_the_placeholder_cue_goes_with_its_label(self):
        plan = _plan(ending_beat="<Subject 7> (Sx) La puerta negra remains shut.")

        _strip_planner_subject_labels(plan)

        self.assertNotIn("(Sx)", plan["ending_beat"])

    def test_a_compiled_prompt_is_never_touched(self):
        # A recompile must be idempotent: the definitions block IS <Subject N>.
        compiled = (
            "subject_definitions: <Subject 1> is Abuela Rosa.\n\n"
            "detailed_description: [Shot 1] She waits."
        )
        plan = _plan(video_prompt=compiled, _director_h3_source_prompt=compiled)

        _strip_planner_subject_labels(plan)

        self.assertEqual(plan["video_prompt"], compiled)
        self.assertEqual(plan["_director_h3_source_prompt"], compiled)

    def test_a_malformed_tag_is_removed_too(self):
        plan = _plan(ending_beat="<Subject q> (S1) the door remains shut.")

        _strip_planner_subject_labels(plan)

        self.assertNotIn("<Subject", plan["ending_beat"])
        self.assertIn("the door remains shut", plan["ending_beat"])

    def test_a_planner_row_cue_is_cleaned_too(self):
        # The checkpoint rows carry ``dialogue_beats`` (the planner's own schema),
        # which is where the black door's phantom label hid.
        plan = _plan(dialogue_beats=[{"physical_cue": "<Subject 7> (Sx) the door stays shut."}])
        plan.pop("_director_dialogue_beats")

        _strip_planner_subject_labels(plan)

        self.assertEqual(plan["dialogue_beats"][0]["physical_cue"].strip(), "the door stays shut.")

    def test_a_plan_without_the_fields_is_safe(self):
        plan = {"video_prompt": "plain", "_director_audio_plan": {"mode": "ambient"}}

        _strip_planner_subject_labels(plan)

        self.assertEqual(plan["video_prompt"], "plain")


if __name__ == "__main__":
    unittest.main()
