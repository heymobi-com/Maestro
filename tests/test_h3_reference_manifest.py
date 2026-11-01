"""A reference manifest from another project must not commit every shot to it.

H3 maps every supplied reference to a Subject and states that the Subject appears in
the shot. Measured on a real 26-clip drama, the manifest was ten references carried
over from two projects: five identity pictures with no role at all, `Roman (S2)` and
`Veronica` (characters that project's brief never mentions), and `Dona Rosa` and
`Los cinco niños del río` with no speaker cue. Every clip therefore declared five
pictures as present, and the priest, the shrouded figure and the children came out
mixed into scenes whose lines named none of them.
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
from services.director_pipeline import (  # noqa: E402
    _h3_continuity_identity_risk,
    _h3_manifest_issues,
    _h3_person_rows,
    _h3_place_keys,
    _h3_positional_labels,
    _h3_references_for_shot,
)

BRIEF = (
    "CHARACTERS AND BINDINGS:\n"
    "S1 (Abuela Rosa): woman, 78 years old, black headscarf.\n"
    "S2 (La Figura del Purgatorio): tall figure wrapped in a black shroud.\n"
    "S4 (Los cinco niños del río): five children in humble clothes.\n"
)


def _image(role: str, filename: str, intent: str = "identity") -> dict:
    return {"type": "image", "role": role, "filename": filename, "image_intent": intent}


MANIFEST = [
    _image("", "style_a.png"),
    _image("", "style_b.png"),
    _image("Dona Rosa", "rosa.png"),
    _image("Roman (S2)", "roman.png"),
    _image("Los cinco niños del río", "ninos.png"),
    _image("Veronica", "veronica.png"),
    {"type": "audio", "role": "Roman (S2)", "filename": "roman.wav", "audio_intent": "voice"},
]


def _clip(prompt: str, cast: list[dict] | None = None) -> dict:
    return {
        "video_prompt": prompt,
        "_director_h3_source_prompt": prompt,
        "_director_environment": "",
        "_director_subjects_on_screen": cast or [],
    }


class ManifestWarningTests(unittest.TestCase):
    def _params(self, brief: str = BRIEF) -> dict:
        return {"minimax_h3_references": MANIFEST, "scene_description": brief}

    def test_role_less_images_are_named(self):
        issues = _h3_manifest_issues(self._params())

        self.assertTrue([i for i in issues if "no role" in i])

    def test_a_character_the_brief_never_mentions_is_named(self):
        issues = _h3_manifest_issues(self._params())

        foreign = [i for i in issues if "brief never mentions" in i]
        self.assertEqual(len(foreign), 1)
        self.assertIn("Roman", foreign[0])
        self.assertIn("Veronica", foreign[0])

    def test_an_identity_without_a_speaker_cue_is_named(self):
        issues = _h3_manifest_issues(self._params())

        unlabelled = [i for i in issues if "carry no (S1)" in i]
        self.assertEqual(len(unlabelled), 1)
        self.assertIn("Dona Rosa", unlabelled[0])

    def test_a_clean_manifest_says_nothing(self):
        params = {
            "minimax_h3_references": [
                _image("Abuela Rosa (S1)", "rosa.png"),
                _image("La Figura (S2)", "figura.png"),
            ],
            "scene_description": BRIEF,
        }

        self.assertEqual(_h3_manifest_issues(params), [])


class PerShotReferenceTests(unittest.TestCase):
    def test_role_less_images_are_not_bound_to_a_shot(self):
        kept = _h3_references_for_shot(
            MANIFEST, _clip("Abuela Rosa stands at the kitchen table.", [
                {"visual_description": "Abuela Rosa", "speaker_name": "(S1)"},
            ]),
        )

        filenames = [r.get("filename") for r in kept]
        self.assertNotIn("style_a.png", filenames)
        self.assertNotIn("style_b.png", filenames)

    def test_an_identity_the_shot_does_not_name_is_not_bound(self):
        kept = _h3_references_for_shot(
            MANIFEST, _clip("Abuela Rosa stands at the kitchen table.", [
                {"visual_description": "Abuela Rosa", "speaker_name": "(S1)"},
            ]),
        )

        filenames = [r.get("filename") for r in kept]
        self.assertIn("rosa.png", filenames)
        self.assertNotIn("veronica.png", filenames)

    def test_a_shot_that_names_nobody_keeps_one_identity(self):
        # Better one likely protagonist than the whole pile as phantom Subjects.
        clip = _clip("A closed black door at the end of a corridor.", [])
        clip["_director_environment"] = "A narrow stone corridor at night."

        kept = _h3_references_for_shot(MANIFEST, clip)

        images = [r for r in kept if r.get("type") == "image"]
        self.assertLessEqual(len(images), 2)
        self.assertTrue(images)

    def test_a_shot_with_no_description_at_all_is_left_alone(self):
        # Nothing to match against is not a reason to drop the user's pictures.
        kept = _h3_references_for_shot(
            MANIFEST, {"video_prompt": "", "_director_environment": "", "_director_subjects_on_screen": []},
        )

        self.assertEqual(len(kept), len(MANIFEST))

    def test_a_labelled_identity_follows_the_labelled_cast(self):
        manifest = [_image("Abuela Rosa (S1)", "rosa.png"), _image("La Figura (S2)", "figura.png")]

        kept = _h3_references_for_shot(
            manifest, _clip("The black shroud opens.", [
                {"visual_description": "La Figura", "speaker_name": "(S2)"},
            ]),
        )

        filenames = [r.get("filename") for r in kept]
        self.assertIn("figura.png", filenames)
        self.assertNotIn("rosa.png", filenames)


class LocationTests(unittest.TestCase):
    """One shot happens in one place, and the reference has to name it."""

    MANIFEST_LOCATIONS = [
        _image("Iglesia", "iglesia.png", "scene"),
        _image("Pueblo", "pueblo.png", "scene"),
        _image("Bosque de noche", "bosque.png", "scene"),
        _image("Mesa con pan y cuchillo", "mesa.png", "scene"),
        _image("Dona Rosa", "rosa.png"),
    ]

    def _clip_in(self, environment: str) -> dict:
        return {
            "video_prompt": f"{environment} Rosa turns.",
            "_director_h3_source_prompt": "",
            "_director_environment": environment,
            "_director_subjects_on_screen": [{"visual_description": "Abuela Rosa"}],
        }

    def _role_of(self, kept):
        return [r.get("role") for r in kept if r.get("image_intent") == "scene"]

    def test_the_forest_reference_is_the_one_bound_to_a_forest_shot(self):
        kept = _h3_references_for_shot(
            self.MANIFEST_LOCATIONS, self._clip_in("A dark twisted forest at night."),
        )

        self.assertEqual(self._role_of(kept), ["Bosque de noche"])

    def test_the_church_reference_is_the_one_bound_to_a_church_shot(self):
        kept = _h3_references_for_shot(
            self.MANIFEST_LOCATIONS,
            self._clip_in("The dark stone interior of the church, candles lit."),
        )

        self.assertEqual(self._role_of(kept), ["Iglesia"])

    def test_a_shot_in_no_named_set_binds_no_set(self):
        # A wrong set is worse than none: the prompt's own prose still describes it.
        kept = _h3_references_for_shot(
            self.MANIFEST_LOCATIONS, self._clip_in("Deep space, stars wheeling."),
        )

        self.assertEqual(self._role_of(kept), [])

    def test_a_spanish_role_matches_an_english_environment(self):
        self.assertEqual(_h3_place_keys("Pueblo"), {"village", "town", "hamlet"})
        self.assertTrue(_h3_place_keys("A cobbled village street") & _h3_place_keys("Pueblo"))

    def test_the_prompt_prose_never_justifies_a_reference(self):
        # The compiled prompt embeds the project's whole cast, so a name found there
        # must not keep a picture: only the clip's own casting does.
        clip = self._clip_in("A dark twisted forest at night.")
        clip["video_prompt"] = "Veronica and the priest stand in the church, pueblo, mesa."

        kept = _h3_references_for_shot(self.MANIFEST_LOCATIONS, clip)

        self.assertNotIn("Veronica", [r.get("role") for r in kept])
        self.assertEqual(self._role_of(kept), ["Bosque de noche"])


class IdentityStabilityTests(unittest.TestCase):
    """A character's own picture must survive every clip, or the face drifts.

    Measured on a real 42-clip documentary: every clip bound exactly one picture —
    David's — and the narrator had none in all 42. Her rows carried `char_0` and no
    inline `(S1)` cue, so `on_screen` was empty, and the labelled picture was dropped
    as "not on screen". The model then invented her hair and face after a few clips.
    """

    MANIFEST = [
        _image("Anita (S1)", "anita.png"),
        _image("David", "david.png"),
        _image("Goliath", "goliath.png"),
    ]
    BRIEF = (
        "SUBJECT LOCK:\n<Subject 1> is ALWAYS Anita.\n\n"
        "CHARACTERS:\nAnita: documentary narrator, cream shirt.\n\n"
        "DESCRIPTION:\nStage 1: Anita narrates the story of David and Goliath.\n"
    )

    def _narrator_only(self) -> dict:
        return {
            "video_prompt": "Anita walks through a study.",
            "_director_subjects_on_screen": [
                {"visual_description": "A young woman with brown hair in a bun.",
                 "character_id": "char_0"},
            ],
        }

    def _biblical_scene(self) -> dict:
        return {
            "video_prompt": "A young man faces a giant.",
            "_director_subjects_on_screen": [
                {"visual_description": "A young man holding a leather sling.",
                 "character_id": "char_1"},
                {"visual_description": "A massive giant in bronze armor."},
                {"visual_description": "The narrator."},
            ],
        }

    def test_the_labelled_narrator_picture_survives_a_shot_that_cannot_name_her(self):
        kept = _h3_references_for_shot(
            self.MANIFEST, self._narrator_only(), brief=self.BRIEF,
        )

        self.assertEqual([r["role"] for r in kept], ["Anita (S1)"])

    def test_a_biblical_scene_keeps_the_faces_it_needs(self):
        kept = _h3_references_for_shot(
            self.MANIFEST, self._biblical_scene(), brief=self.BRIEF,
        )

        roles = [r["role"] for r in kept]
        self.assertIn("David", roles)
        self.assertIn("Goliath", roles)
        self.assertIn("Anita (S1)", roles)

    def test_the_narrator_alone_does_not_drag_the_whole_cast_into_her_shot(self):
        kept = _h3_references_for_shot(
            self.MANIFEST, self._narrator_only(), brief=self.BRIEF,
        )

        self.assertNotIn("David", [r["role"] for r in kept])
        self.assertNotIn("Goliath", [r["role"] for r in kept])

    def test_a_character_the_brief_never_mentions_is_still_a_leftover(self):
        manifest = self.MANIFEST + [_image("Veronica", "veronica.png")]

        kept = _h3_references_for_shot(
            manifest, self._biblical_scene(), brief=self.BRIEF,
        )

        self.assertNotIn("Veronica", [r["role"] for r in kept])

    def test_the_picture_is_stable_across_every_clip_of_a_brief(self):
        # The measured failure: 0 of 42 clips had the narrator's own face.
        for clip in (self._narrator_only(), self._biblical_scene()):
            roles = [r["role"] for r in _h3_references_for_shot(
                self.MANIFEST, clip, brief=self.BRIEF,
            )]
            self.assertIn("Anita (S1)", roles)

    def test_a_positional_id_maps_onto_the_manifest_order(self):
        # `char_0` with no inline cue is the first identity the user supplied.
        self.assertEqual(_h3_positional_labels(self._narrator_only(), self.MANIFEST), {"S1"})


class PersonRowTests(unittest.TestCase):
    def test_a_dressed_or_holding_row_is_a_person(self):
        for description in (
            "An ancient scholar holding a rolled scroll, wearing rough linen garments.",
            "A young man with curly dark hair, wearing a tan tunic.",
            "A massive giant with long dark hair and beard.",
            "The narrator.",
        ):
            clip = {"_director_subjects_on_screen": [{"visual_description": description}]}
            self.assertEqual(_h3_person_rows(clip), 1, description)

    def test_a_place_or_an_object_is_not_a_person(self):
        for description in (
            "A narrow black wooden door with no handle.",
            "A broken bronze bell hanging from a rope.",
            "An empty cobblestone street at dawn.",
        ):
            clip = {"_director_subjects_on_screen": [{"visual_description": description}]}
            self.assertEqual(_h3_person_rows(clip), 0, description)


class ContinuityRiskTests(unittest.TestCase):
    """Clip continuity re-renders the previous face, so it drifts over a long film."""

    PARAMS = {
        "_director_sequence_continuity": True,
        "minimax_h3_references": [
            _image("Anita (S1)", "anita.png"),
            _image("David", "david.png"),
        ],
    }

    def test_a_chained_film_with_a_labelled_identity_is_reported(self):
        issues = _h3_continuity_identity_risk(self.PARAMS, 42)

        self.assertEqual(len(issues), 1)
        self.assertIn("Clip continuity", issues[0])
        self.assertIn("Anita", issues[0])
        self.assertIn("42 clips", issues[0])

    def test_the_independent_workflow_is_not_reported(self):
        params = dict(self.PARAMS, _director_sequence_continuity=False)

        self.assertEqual(_h3_continuity_identity_risk(params, 42), [])

    def test_a_short_film_is_not_reported(self):
        # A handful of chained clips does not visibly drift; the warning would be noise.
        self.assertEqual(_h3_continuity_identity_risk(self.PARAMS, 6), [])

    def test_a_manifest_without_a_labelled_identity_is_not_reported(self):
        params = dict(self.PARAMS, minimax_h3_references=[_image("Anita", "anita.png")])

        self.assertEqual(_h3_continuity_identity_risk(params, 42), [])


class PublishTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(director_pipeline._pipelines)
        director_pipeline._pipelines.clear()
        director_pipeline._pipelines["m1"] = {"status": "running", "plan_warnings": ["existing"]}

    def tearDown(self):
        director_pipeline._pipelines.clear()
        director_pipeline._pipelines.update(self._saved)

    def test_issues_join_the_plan_warnings_shown_in_the_composer(self):
        with contextlib.redirect_stdout(io.StringIO()):
            director_pipeline._report_h3_manifest_issues(
                "m1", {"minimax_h3_references": MANIFEST, "scene_description": BRIEF},
            )

        warnings = director_pipeline._pipelines["m1"]["plan_warnings"]
        self.assertIn("existing", warnings)
        self.assertTrue([w for w in warnings if "no role" in w])


if __name__ == "__main__":
    unittest.main()
