"""Editing a saved character must change it in place, never replace its id."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from services import character_library  # noqa: E402


class CharacterEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maestro-characters-"))
        self.uploads = self.tmp / "uploads"
        self.uploads.mkdir()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self._root_patch = mock.patch.object(
            character_library, "_root", return_value=self.tmp / "characters"
        )
        self._root_patch.start()
        (self.tmp / "characters").mkdir()
        self._probe_patch = mock.patch.object(
            character_library, "_probe_duration", side_effect=lambda path: 4.0
        )
        self._probe_patch.start()
        self._audio_patch = mock.patch.object(
            character_library, "_video_has_audio", return_value=False
        )
        self._audio_patch.start()

    def tearDown(self):
        self._audio_patch.stop()
        self._probe_patch.stop()
        self._root_patch.stop()
        os.chdir(self._cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _upload(self, name: str, data: bytes = b"data") -> str:
        path = self.uploads / name
        path.write_bytes(data)
        return str(path)

    def _create(self, name="Bela"):
        return character_library.create_character(
            name=name,
            visual_path=self._upload("bela.png"),
            visual_type="image",
            voice_path=self._upload("bela.wav"),
        )

    def test_replacing_the_picture_keeps_the_same_id_and_updates_the_file(self):
        created = self._create()
        original_id = created["id"]

        updated = character_library.update_character(
            original_id,
            visual_path=self._upload("bela-new.jpg"),
            visual_type="image",
        )

        self.assertEqual(updated["id"], original_id)
        self.assertEqual(updated["name"], "Bela")
        self.assertTrue(updated["visual"]["path"].endswith("visual.jpg"))
        self.assertEqual(updated["visual"]["filename"], "bela-new.jpg")
        self.assertGreater(updated["updated_at"], created["updated_at"] - 1)
        directory = self.tmp / "characters" / original_id
        self.assertFalse((directory / "visual.png").exists(), "the old picture must not linger")
        self.assertTrue((directory / "visual.jpg").exists())

    def test_replacing_the_voice_keeps_the_character_and_its_picture(self):
        created = self._create()

        updated = character_library.update_character(
            created["id"], voice_path=self._upload("bela-other.mp3")
        )

        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["voice"]["filename"], "bela-other.mp3")
        self.assertTrue(updated["voice"]["path"].endswith("voice.mp3"))
        self.assertEqual(updated["visual"]["filename"], created["visual"]["filename"])
        directory = self.tmp / "characters" / created["id"]
        self.assertFalse((directory / "voice.wav").exists())

    def test_renaming_keeps_the_media(self):
        created = self._create()

        updated = character_library.update_character(created["id"], name="  Bela  Prime ")

        self.assertEqual(updated["name"], "Bela Prime")
        self.assertEqual(updated["visual"]["filename"], created["visual"]["filename"])
        self.assertEqual(updated["voice"]["filename"], created["voice"]["filename"])

    def test_an_empty_name_is_refused(self):
        created = self._create()

        with self.assertRaises(ValueError):
            character_library.update_character(created["id"], name="   ")

    def test_an_unknown_character_is_reported(self):
        with self.assertRaises(ValueError):
            character_library.update_character("0123456789abcdef", name="Nobody")

    def test_replacing_the_picture_drops_stale_views_and_latent(self):
        created = self._create()
        directory = self.tmp / "characters" / created["id"]
        (directory / "reference.safetensors").write_bytes(b"latent")
        index_path = character_library._index_path()
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["characters"][0]["image_views"] = {"version": 1, "items": [], "selected_ids": []}
        data["characters"][0]["refmod"] = {"path": "stale"}
        index_path.write_text(json.dumps(data), encoding="utf-8")

        updated = character_library.update_character(
            created["id"], visual_path=self._upload("bela-2.png"), visual_type="image"
        )

        self.assertIsNone(updated.get("image_views"))
        self.assertIsNone(updated.get("refmod"))
        self.assertFalse((directory / "reference.safetensors").exists())


if __name__ == "__main__":
    unittest.main()
