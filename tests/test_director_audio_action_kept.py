"""An audio-driven drama must keep the performance it planned.

Reported from use: "le he dado multiples referencias, el prompt variado y no sale de
la misma escena estatica de un personaje ... necesito mas riqueza en las escenas ya
que es una historia no un podcast".

Measured on the affected run (``_director_pipeline_20a3897e``, 26 clips, Underdog
Saluki 27B). The planner was good: it wrote, per clip, a chronological performance --
"She touches the knife with her knotted veined hands, then touches the rosary at her
chest", "Don Aurelio ... enters from behind her with his head tilted to one side",
"Streetlamps flicker in unison and fail one by one". None of it reached the renderer.

``_compress_a2v`` kept the first two sentences (the setting), any sentence containing a
quote or one of ``says/speaks/whispers/gestures/nods``, and the last sentence, and
deleted the rest. Every sentence describing something happening went with it, and seven
consecutive shots in the same set reduced to the same three sentences:

    903 chars -> 235 chars
    "[Shot 1] Static locked-off medium shot with slow creeping push-in, 35mm
     anamorphic, shallow depth of field. In a small stone kitchen in Valdecuna,
     dawn-grey light filters through a window with fog outside. No new dialogue
     is generated."

so those clips could only render the same static portrait of the same character. The
budget it exists for is real, so the replacement trims what the kept text already said
and never the performance.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.schema import ShotPlan  # noqa: E402
from services.director.validators.compressor import compress_prompt  # noqa: E402
from services.director_pipeline import _h3_references_for_shot  # noqa: E402

# The planner's own words for a kitchen clip of that run, verbatim.
PLANNED = (
    "[Shot 1] Static locked-off medium shot with slow creeping push-in, 35mm anamorphic, "
    "shallow depth of field. In a small stone kitchen in Valdecuna, dawn-grey light "
    "filters through a window with fog outside. Abuela Rosa, a 78-year-old woman with "
    "grey hair under a black headscarf, deep wrinkles, and a wooden rosary at her neck, "
    "stands before a wooden table holding a plate, a steaming cup, and a knife driven "
    "into the wood. She touches the knife with her knotted veined hands, then touches "
    "the rosary at her chest. The black door without handle is ajar in the background, "
    "blue light spills from behind it. Don Aurelio, a tall gaunt figure wrapped in a "
    "trailing black sheet with bare pale feet and a smooth erased face, enters from "
    "behind her with his head tilted to one side, bronze bells swaying at his neck. "
    "Rosa turns to look at him. Her lips move in sync with the audio. No new dialogue "
    "is generated."
)


def _dialogue_shot(**overrides) -> ShotPlan:
    raw = {
        "shot_id": "sf_1",
        "index": 0,
        "duration_sec": 10.0,
        "skill_type": "short_film",
        "scene_goal": "Rosa faces what came through the door",
        "subjects_on_screen": [],
        "spatial_setup": "",
        "environment": "A small stone kitchen in Valdecuna.",
        "visual_style": "35mm anamorphic",
        "lighting": "dawn-grey",
        "mood": "dread",
        "action_beats": [],
        "camera_plan": {"framing": "medium shot"},
        "ending_beat": "",
        "audio_plan": {"mode": "audio_driven", "lip_sync_critical": True},
    }
    raw.update(overrides)
    return ShotPlan.from_dict(raw)


class ThePerformanceSurvivesCompressionTests(unittest.TestCase):
    def test_every_action_of_the_planned_shot_reaches_the_renderer(self):
        compressed = compress_prompt(PLANNED, "a2v", _dialogue_shot()).compressed

        for action in (
            "touches the knife",
            "touches the rosary at her chest",
            "blue light spills from behind it",
            "enters from behind her",
            "bells swaying at his neck",
            "Rosa turns to look at him",
        ):
            with self.subTest(action=action):
                self.assertIn(action, compressed)

    def test_the_shot_no_longer_collapses_to_setting_alone(self):
        compiled = compress_prompt(PLANNED, "a2v", _dialogue_shot())

        self.assertGreater(len(compiled.compressed), 700)
        self.assertLess(compiled.chars_removed, 200)

    def test_two_different_shots_stay_different(self):
        church = PLANNED.replace(
            "In a small stone kitchen in Valdecuna, dawn-grey light filters through a "
            "window with fog outside",
            "In a small village church, golden light from candles and incense haze "
            "fills the space",
        ).replace("She touches the knife", "She kneels on the stone floor")

        kitchen_text = compress_prompt(PLANNED, "a2v", _dialogue_shot()).compressed
        church_text = compress_prompt(church, "a2v", _dialogue_shot()).compressed

        self.assertNotEqual(kitchen_text, church_text)
        self.assertIn("kneels on the stone floor", church_text)

    def test_a_restated_setting_is_still_trimmed(self):
        repeated = (
            "A medium shot in a small stone kitchen in Valdecuna, dawn-grey light "
            "filters through a window with fog outside. In a small stone kitchen in "
            "Valdecuna, dawn-grey light filters through a window with fog outside. "
            "The wooden table holds a plate and a steaming cup. Rosa lifts the knife "
            "from the wood. The black door stands ajar behind her. The candle stub "
            "gutters on the sill. No new dialogue is generated."
        )

        compressed = compress_prompt(repeated, "a2v", _dialogue_shot()).compressed

        self.assertLess(len(compressed), len(repeated))
        self.assertIn("Rosa lifts the knife", compressed)
        # The setting said twice is said once.
        self.assertEqual(compressed.count("filters through a window with fog outside"), 1)

    def test_a_short_prompt_is_left_alone(self):
        short = "A medium shot in the kitchen. Rosa turns to the door. No new dialogue is generated."

        self.assertEqual(
            compress_prompt(short, "a2v", _dialogue_shot()).compressed,
            short,
        )

    def test_a_shot_without_lip_sync_keeps_the_old_passthrough(self):
        shot = _dialogue_shot(
            audio_plan={"mode": "ambient_only", "lip_sync_critical": False},
        )

        self.assertEqual(compress_prompt(PLANNED, "a2v", shot).compressed, PLANNED)


KITCHEN_SHOT = {
    "video_prompt": (
        "[Shot 1] In a small stone kitchen in Valdecuna, dawn-grey light filters "
        "through a window with fog outside."
    ),
    "_director_environment": "Small stone kitchen in Valdecuna.",
    "_director_subjects_on_screen": [{"visual_description": "Abuela Rosa (S1)"}],
}


def _locations(*roles: str) -> list[dict]:
    references = [
        {
            "type": "image",
            "path": f"loc{index}.png",
            "role": role,
            "image_intent": "scene",
        }
        for index, role in enumerate(roles)
    ]
    references.append({
        "type": "image",
        "path": "rosa.png",
        "role": "Dona Rosa",
        "image_intent": "identity",
    })
    return references


class OneShotIsInOnePlaceTests(unittest.TestCase):
    """Every supplied location used to travel into every clip.

    The measured run gave its kitchen shot five environments at once -- church,
    corridor, street, kitchen, forest edge -- and every clip rendered in the village
    street.
    """

    def test_unnamed_locations_are_not_all_bound_to_one_shot(self):
        references = _locations("", "", "", "", "")

        kept = _h3_references_for_shot(references, KITCHEN_SHOT)

        self.assertEqual([r["path"] for r in kept if r.get("image_intent") == "scene"], [])
        # The cast still travels: the shot needs its people.
        self.assertIn("rosa.png", [r["path"] for r in kept])

    def test_a_named_location_is_selected_by_the_shot_it_belongs_to(self):
        references = _locations("the church altar", "the stone kitchen", "the street")

        kept = _h3_references_for_shot(references, KITCHEN_SHOT)

        self.assertEqual(
            [r["path"] for r in kept if r.get("image_intent") == "scene"],
            ["loc1.png"],
        )

    def test_a_single_location_stays_unambiguous(self):
        references = _locations("")

        kept = _h3_references_for_shot(references, KITCHEN_SHOT)

        self.assertEqual(
            [r["path"] for r in kept if r.get("image_intent") == "scene"],
            ["loc0.png"],
        )

    def test_the_generic_default_role_does_not_match_every_shot(self):
        references = _locations(
            "the environment and location shown in this reference",
            "the environment and location shown in this reference",
        )

        kept = _h3_references_for_shot(references, KITCHEN_SHOT)

        self.assertEqual([r["path"] for r in kept if r.get("image_intent") == "scene"], [])


if __name__ == "__main__":
    unittest.main()
