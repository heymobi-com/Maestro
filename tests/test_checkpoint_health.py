"""Imported checkpoints must not outlive their weights: the audit, the prune and the removal.

The reported case is the fixture: one CivitAI import is a weight file, a provenance sidecar and
one registration per workflow, so deleting the weights by hand left two registrations behind that
kept the checkpoint listed, kept the model list offering it, and had no way out from the interface.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services import checkpoint_health  # noqa: E402
from services.checkpoint_health import (  # noqa: E402
    clear_removed_models,
    forget_missing_definitions,
    mark_missing,
    remove_imported_checkpoint,
    router,
)

FILENAME = "h3ErosMax_beta5_3185154.safetensors"


def registration(*, file_id=3185154, filename=FILENAME, name="H3 Eros Max"):
    return {
        "model": {
            "name": name,
            "architecture": "minimax_h3",
            "URLs": [filename],
            "civitai": {
                "modelId": 2851079,
                "versionId": 3294059,
                "fileId": file_id,
                "filename": filename,
                "sha256": "a" * 64,
                "size_bytes": 10,
            },
        },
        "num_inference_steps": 20,
    }


class MarkMissingTests(unittest.TestCase):
    def test_a_working_install_and_a_deleted_one_are_told_apart(self):
        entries = [{"filename": "there.safetensors"}, {"filename": "gone.safetensors"}]
        marked = mark_missing(
            entries,
            locate=lambda name: "/ckpts/there.safetensors" if name == "there.safetensors" else None,
        )
        self.assertEqual([row["missing"] for row in marked], [False, True])

    def test_the_caller_entries_are_not_mutated(self):
        original = [{"filename": "gone.safetensors"}]
        marked = mark_missing(original, locate=lambda name: None)
        self.assertNotIn("missing", original[0])
        self.assertTrue(marked[0]["missing"])


class ForgetMissingDefinitionsTests(unittest.TestCase):
    def test_only_definitions_with_a_file_on_disk_survive(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "finetunes"))
            Path(root, "finetunes", "kept.json").write_text("{}", encoding="utf-8")
            registry = {"kept": {"name": "kept"}, "orphan": {"name": "orphan"}}
            removed = forget_missing_definitions(registry, root)
            self.assertEqual(removed, ["orphan"])
            self.assertEqual(sorted(registry), ["kept"])

    def test_a_defaults_definition_counts_as_present(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "defaults"))
            Path(root, "defaults", "shipped.json").write_text("{}", encoding="utf-8")
            registry = {"shipped": {}}
            self.assertEqual(forget_missing_definitions(registry, root), [])
            self.assertEqual(sorted(registry), ["shipped"])


class ClearRemovedModelsTests(unittest.TestCase):
    def test_every_map_that_can_name_a_model_is_cleared(self):
        preferences = {
            "selected_model_per_mode": {"video": "gone", "image": "kept"},
            "selected_model_per_audio_sub_mode": {"music": "gone", "speech": "kept"},
            "inference_steps_per_model": {"gone": 8, "kept": 20},
            "director_max_shot_frames_per_model": {"gone": 60},
            "h3_optimizations": {"override_attention": ""},
        }
        updated, cleared = clear_removed_models(preferences, ["gone"])
        self.assertEqual(updated["selected_model_per_mode"], {"image": "kept"})
        self.assertEqual(updated["selected_model_per_audio_sub_mode"], {"speech": "kept"})
        self.assertEqual(updated["inference_steps_per_model"], {"kept": 20})
        self.assertEqual(updated["director_max_shot_frames_per_model"], {})
        self.assertEqual(updated["h3_optimizations"], {"override_attention": ""})
        self.assertEqual(
            sorted(cleared),
            [
                "director_max_shot_frames_per_model",
                "inference_steps_per_model",
                "selected_model_per_audio_sub_mode",
                "selected_model_per_mode",
            ],
        )
        self.assertEqual(preferences["selected_model_per_mode"]["video"], "gone")

    def test_a_selection_that_names_nothing_removed_is_untouched(self):
        updated, cleared = clear_removed_models({"selected_model_per_mode": {"video": "kept"}}, ["other"])
        self.assertEqual(cleared, [])
        self.assertEqual(updated, {"selected_model_per_mode": {"video": "kept"}})


class RemoveImportedCheckpointTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = self._temporary.name
        self.finetunes = os.path.join(self.root, "finetunes")
        self.checkpoints = os.path.join(self.root, "ckpts")
        os.makedirs(self.finetunes)
        os.makedirs(self.checkpoints)

    def tearDown(self):
        self._temporary.cleanup()

    def register(self, model_type, **overrides):
        Path(self.finetunes, model_type + ".json").write_text(
            json.dumps(registration(**overrides)), encoding="utf-8"
        )

    def locate(self, name):
        path = os.path.join(self.checkpoints, name)
        return path if os.path.isfile(path) else None

    def remove(self, model_type, *, protected=False):
        return remove_imported_checkpoint(
            model_type,
            finetunes_dir=self.finetunes,
            checkpoint_dir=self.checkpoints,
            locate=self.locate,
            protected=lambda path: protected,
        )

    def test_both_workflows_of_one_import_go_together(self):
        self.register("civitai_h3_2851079_3294059_3185154_frames")
        self.register("civitai_h3_2851079_3294059_3185154_references")
        Path(self.checkpoints, FILENAME).write_bytes(b"weights")
        Path(self.checkpoints, "h3ErosMax_beta5_3185154.civitai.json").write_text("{}", encoding="utf-8")

        receipt = self.remove("civitai_h3_2851079_3294059_3185154_frames")

        self.assertEqual(
            receipt["registrations"],
            [
                "civitai_h3_2851079_3294059_3185154_frames",
                "civitai_h3_2851079_3294059_3185154_references",
            ],
        )
        self.assertEqual(receipt["deleted_weights"], [FILENAME])
        self.assertEqual(receipt["deleted_sidecars"], ["h3ErosMax_beta5_3185154.civitai.json"])
        self.assertEqual(os.listdir(self.finetunes), [])
        self.assertEqual(os.listdir(self.checkpoints), [])

    def test_the_reported_case_removes_what_is_left(self):
        self.register("civitai_h3_2851079_3294059_3185154_frames")
        self.register("civitai_h3_2851079_3294059_3185154_references")
        Path(self.checkpoints, "h3ErosMax_beta5_3185154.civitai.json").write_text("{}", encoding="utf-8")

        receipt = self.remove("civitai_h3_2851079_3294059_3185154_references")

        self.assertEqual(receipt["deleted_weights"], [])
        self.assertEqual(receipt["deleted_sidecars"], ["h3ErosMax_beta5_3185154.civitai.json"])
        self.assertEqual(os.listdir(self.finetunes), [])

    def test_a_weight_in_a_linked_folder_is_kept_and_reported(self):
        self.register("civitai_h3_2851079_3294059_3185154_frames")
        Path(self.checkpoints, FILENAME).write_bytes(b"weights")

        receipt = self.remove("civitai_h3_2851079_3294059_3185154_frames", protected=True)

        self.assertEqual(receipt["deleted_weights"], [])
        self.assertEqual(receipt["kept_weights"], [FILENAME])
        self.assertTrue(os.path.isfile(os.path.join(self.checkpoints, FILENAME)))

    def test_another_import_of_the_same_model_survives(self):
        self.register("civitai_h3_2851079_3294059_3185154_frames")
        self.register("civitai_h3_2851079_3294059_3185154_references")
        self.register(
            "civitai_h3_2851079_3263322_3146849_references",
            file_id=3146849,
            filename="h3ErosMax_beta3_3146849.safetensors",
        )

        self.remove("civitai_h3_2851079_3294059_3185154_frames")

        self.assertEqual(sorted(os.listdir(self.finetunes)), ["civitai_h3_2851079_3263322_3146849_references.json"])

    def test_an_unregistered_name_is_not_found(self):
        with self.assertRaises(KeyError):
            self.remove("civitai_h3_0000000_0000000_0000000_frames")


class RouterTests(unittest.TestCase):
    def test_the_removal_is_reachable_at_its_own_path(self):
        routes = {(route.path, tuple(sorted(route.methods))) for route in router.routes}
        self.assertIn(("/api/v1/checkpoints/{model_type}", ("DELETE",)), routes)


class ForgetEverywhereTests(unittest.TestCase):
    """The endpoint's second half: the live registry and the saved selection let go."""

    def fake_wgp(self, directory, preferences):
        module = types.ModuleType("wgp")
        module.models_def = {"removed": {"name": "removed"}, "kept": {"name": "kept"}}
        module.displayed_model_types = ["removed", "kept"]
        module.server_config = {"maestro_studio_preferences": preferences}
        module.server_config_filename = os.path.join(directory, "wgp_config.json")
        return module

    def test_the_registry_and_the_remembered_selection_forget_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = self.fake_wgp(directory, {
                "selected_model_per_mode": {"video": "removed", "image": "kept"},
                "inference_steps_per_model": {"removed": 8, "kept": 20},
            })
            with patch.dict(sys.modules, {"wgp": fake}):
                cleared = checkpoint_health._forget_everywhere(["removed"])

            self.assertEqual(sorted(fake.models_def), ["kept"])
            self.assertEqual(fake.displayed_model_types, ["kept"])
            self.assertEqual(
                sorted(cleared), ["inference_steps_per_model", "selected_model_per_mode"]
            )
            persisted = json.loads(Path(fake.server_config_filename).read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["maestro_studio_preferences"]["selected_model_per_mode"], {"image": "kept"}
            )

    def test_a_selection_that_named_nothing_removed_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = self.fake_wgp(directory, {"selected_model_per_mode": {"video": "kept"}})
            with patch.dict(sys.modules, {"wgp": fake}):
                cleared = checkpoint_health._forget_everywhere(["something-else"])

            self.assertEqual(cleared, [])
            self.assertEqual(sorted(fake.models_def), ["kept", "removed"])
            self.assertFalse(
                os.path.exists(fake.server_config_filename), "nothing changed, nothing written"
            )


if __name__ == "__main__":
    unittest.main()
