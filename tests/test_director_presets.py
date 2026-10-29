"""A Director setup you can start from again, without re-entering the project.

Reported from use: *"cuando algo falla no puedo regresar a corregir sino que tengo que
volver a meter todo: personajes, referencias, volver a correr ... en este proceso de
ensayo y error esto se debe hacer muchas veces"*, and *"hay una funcion de save recipe
supongo que guarda todos los parametros, pero no se puede servir a modo director"*.

A Studio recipe is a look preset and ``recipes.RECIPE_PARAM_KEYS`` leaves out the
per-generation inputs on purpose, so it can never rebuild a Director project: the cast
images, the soundtrack and its transcript are the setup. These presets carry them, and
carry no plan, so applying one cannot re-render the shot that just failed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services import director_presets as presets  # noqa: E402
from services.director_pipeline import _director_asset_filename  # noqa: E402


def _png(path: Path, payload: bytes = b"\x89PNG\r\n\x1a\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class PresetFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.out_dir = Path(tempfile.mkdtemp(prefix="maestro-presets-"))
        self.addCleanup(shutil.rmtree, self.out_dir, ignore_errors=True)
        assets = self.out_dir / "_director_assets" / "abc123"
        _png(assets / "minimax_h3_references_1_visual.png", b"rosa")
        _png(assets / "minimax_h3_references_2_visual.png", b"figure")
        (assets / "audio_path_d5141934.wav").write_bytes(b"RIFFfake")
        self.state = {
            "pipeline_id": "abc123",
            "workspace": "Valdecuna",
            "_params_snapshot": {
                "pipeline_type": "short_film_audio",
                "video_model": "minimax_h3_ref2va_fused_turbo",
                "scene_description": "SUBJECT LOCK:\n- Subject 1 is ALWAYS Abuela Rosa.",
                "audio_path": str(assets / "audio_path_d5141934.wav"),
                "minimax_h3_references": [
                    {
                        "type": "image",
                        "path": str(assets / "minimax_h3_references_1_visual.png"),
                        "role": "Dona Rosa",
                        "image_intent": "identity",
                    },
                    {
                        "type": "image",
                        "path": str(assets / "minimax_h3_references_2_visual.png"),
                        "role": "La Figura",
                        "image_intent": "identity",
                    },
                ],
                # Run-scoped: must never come back from a preset.
                "planned_clips": [{"start": 0, "end": 8}],
                "prepared_clip_plans": [{"video_prompt": "the shot that failed"}],
                "_director_planning_checkpoint": {"stage": "planning"},
                "_director_project_id": "abc123",
                "director_ui_snapshot": {
                    "directorStep": "review_video",
                    "directorSceneDescription": "SUBJECT LOCK:\n- Subject 1 is ALWAYS Abuela Rosa.",
                    "directorAnalysis": {"duration": 120.0, "sections": []},
                    "directorSpeakerMappings": [{"speaker": "SPEAKER_00", "name": "Rosa"}],
                    "directorClipPlans": [{"video_prompt": "the shot that failed"}],
                    "directorPlannedClips": [{"start": 0, "end": 8}],
                    "directorLlmLog": [{"stage": "streaming_1", "text": "..."}],
                },
            },
        }

    def save(self, name: str = "Valdecuna drama") -> dict:
        return presets.save_preset(
            self.state, name, str(self.out_dir), source_pipeline_id="abc123",
        )


class TheSetupSurvivesTests(PresetFixture):
    def test_the_brief_the_cast_and_the_soundtrack_come_back(self):
        card = self.save()
        payload = presets.get_preset(card["id"], str(self.out_dir))
        params = payload["params"]

        self.assertIn("Abuela Rosa", params["scene_description"])
        self.assertEqual(len(params["minimax_h3_references"]), 2)
        self.assertTrue(os.path.isfile(params["audio_path"]))
        for reference in params["minimax_h3_references"]:
            with self.subTest(reference=reference["role"]):
                self.assertTrue(os.path.isfile(reference["path"]))
                # Owned by the preset, not by the run it was frozen from.
                self.assertIn(presets.PRESET_CONTAINER, reference["path"])

    def test_the_transcript_is_kept_so_planning_does_not_transcribe_again(self):
        card = self.save()
        ui = presets.get_preset(card["id"], str(self.out_dir))["params"]["director_ui_snapshot"]

        self.assertEqual(ui["directorAnalysis"]["duration"], 120.0)
        self.assertEqual(ui["directorSpeakerMappings"][0]["name"], "Rosa")

    def test_the_models_and_controls_travel(self):
        card = self.save()
        params = presets.get_preset(card["id"], str(self.out_dir))["params"]

        self.assertEqual(params["video_model"], "minimax_h3_ref2va_fused_turbo")
        self.assertEqual(params["pipeline_type"], "short_film_audio")

    def test_the_card_says_what_the_setup_contains(self):
        card = self.save()

        self.assertEqual(card["reference_count"], 2)
        self.assertTrue(card["has_transcript"])
        self.assertEqual(card["brief_chars"], len(
            "SUBJECT LOCK:\n- Subject 1 is ALWAYS Abuela Rosa."
        ))
        self.assertEqual(card["source_pipeline_id"], "abc123")


class NoPlanComesBackTests(PresetFixture):
    """A preset is a setup. Restoring the previous plan would re-render the failure."""

    def test_the_previous_prompts_and_timeline_are_stripped(self):
        card = self.save()
        params = presets.get_preset(card["id"], str(self.out_dir))["params"]
        ui = params["director_ui_snapshot"]

        for key in ("planned_clips", "prepared_clip_plans", "_director_planning_checkpoint"):
            with self.subTest(param=key):
                self.assertNotIn(key, params)
        for key in ("directorClipPlans", "directorPlannedClips", "directorLlmLog"):
            with self.subTest(ui=key):
                self.assertNotIn(key, ui)

    def test_the_run_identity_is_not_inherited(self):
        card = self.save()
        params = presets.get_preset(card["id"], str(self.out_dir))["params"]

        self.assertNotIn("_director_project_id", params)


class ListingAndDeletingTests(PresetFixture):
    def test_presets_are_listed_newest_first(self):
        first = self.save("First")
        second = presets.save_preset(
            self.state, "Second", str(self.out_dir), source_pipeline_id="abc123",
        )

        listed = presets.list_presets(str(self.out_dir))

        self.assertEqual({card["id"] for card in listed}, {first["id"], second["id"]})
        self.assertEqual(len(listed), 2)

    def test_deleting_removes_the_setup_and_its_assets(self):
        card = self.save()
        payload = presets.get_preset(card["id"], str(self.out_dir))
        audio = payload["params"]["audio_path"]

        self.assertTrue(presets.delete_preset(card["id"], str(self.out_dir)))

        self.assertFalse(os.path.isfile(audio))
        self.assertIsNone(presets.get_preset(card["id"], str(self.out_dir)))
        self.assertEqual(presets.list_presets(str(self.out_dir)), [])

    def test_an_unexpected_id_is_refused(self):
        self.assertIsNone(presets.get_preset("../../etc/passwd", str(self.out_dir)))
        self.assertFalse(presets.delete_preset("../boom", str(self.out_dir)))

    def test_an_empty_workspace_has_no_presets(self):
        self.assertEqual(presets.list_presets(str(self.out_dir / "missing")), [])


class SavingRefusesWhatItCannotFreezeTests(PresetFixture):
    def test_a_run_without_a_setup_snapshot_says_what_to_do(self):
        with self.assertRaises(ValueError) as caught:
            presets.save_preset({"pipeline_id": "old"}, "Name", str(self.out_dir))

        self.assertIn("no saved setup", str(caught.exception))

    def test_a_preset_needs_a_name(self):
        with self.assertRaises(ValueError) as caught:
            presets.save_preset(self.state, "   ", str(self.out_dir))

        self.assertIn("name is required", str(caught.exception))

    def test_an_empty_setup_says_what_to_add(self):
        with self.assertRaises(ValueError) as caught:
            presets.save_preset(
                {"_params_snapshot": {"pipeline_type": "short_film_audio"}},
                "Empty", str(self.out_dir),
            )

        self.assertIn("nothing to save yet", str(caught.exception))

    def test_a_file_that_is_no_longer_on_disk_is_named(self):
        """A setup that restores with a missing image fails later, inside a render."""

        params = dict(self.state["_params_snapshot"])
        params["minimax_h3_references"] = [
            {
                "type": "image",
                "path": str(self.out_dir / "gone.png"),
                "role": "Missing ref",
                "image_intent": "identity",
            },
        ]

        with self.assertRaises(ValueError) as caught:
            presets.save_preset({"_params_snapshot": params}, "Broken", str(self.out_dir))

        self.assertIn("Missing ref", str(caught.exception))

    def test_the_same_name_twice_makes_two_setups(self):
        first = self.save("Same")
        second = self.save("Same")

        self.assertNotEqual(first["id"], second["id"])


class ASetupCanBeSavedBeforeAnyRunTests(PresetFixture):
    """The composer's own setup, with no saved run behind it.

    The bar used to appear only once a run existed, which is the one moment the user is
    not looking for it: the loop is edit, plan, fail, edit again.
    """

    def test_the_live_composer_setup_is_enough(self):
        params = {
            "pipeline_type": "short_film_audio",
            "workspace": "default",
            "scene_description": "SUBJECT LOCK:\n- Subject 1 is ALWAYS Abuela Rosa.",
            "minimax_h3_references": self.state["_params_snapshot"]["minimax_h3_references"],
            "audio_path": self.state["_params_snapshot"]["audio_path"],
            "director_ui_snapshot": {"directorAnalysis": {"duration": 30.0}},
        }

        card = presets.save_preset(
            {"_params_snapshot": params, "workspace": "default"},
            "From the composer",
            str(self.out_dir),
        )
        payload = presets.get_preset(card["id"], str(self.out_dir))

        self.assertEqual(card["reference_count"], 2)
        self.assertEqual(payload["source_pipeline_id"], "")
        self.assertTrue(payload["params"]["director_ui_snapshot"]["directorAnalysis"])
        for reference in payload["params"]["minimax_h3_references"]:
            with self.subTest(reference=reference["role"]):
                self.assertTrue(os.path.isfile(reference["path"]))


class AssetNamesDoNotCompoundTests(unittest.TestCase):
    """Measured while building this: ``audio_path_audio_path_audio_path_8802b1b4.wav``."""

    def test_an_already_namespaced_file_keeps_its_name(self):
        once = _director_asset_filename("audio_path", None, "/up/audio_path_8802.wav")
        twice = _director_asset_filename("audio_path", None, f"/run/{once}")

        self.assertEqual(once, "audio_path_8802.wav")
        self.assertEqual(twice, once)

    def test_a_plain_upload_is_namespaced_once(self):
        self.assertEqual(
            _director_asset_filename("character_ref_paths", 2, "/up/portrait.png"),
            "character_ref_paths_3_portrait.png",
        )


if __name__ == "__main__":
    unittest.main()
