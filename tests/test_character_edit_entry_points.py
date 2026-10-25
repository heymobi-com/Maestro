"""Editing a character must be reachable where the character is chosen."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "ui" / "src" / "components" / "Sidebar"
CHARACTERS = ROOT / "ui" / "src" / "components" / "Characters"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class CharacterEditEntryPoints(unittest.TestCase):
    def test_one_dialog_serves_every_entry_point(self):
        dialog = _read(CHARACTERS / "CharacterEditDialog.tsx")

        self.assertIn("export function CharacterEditDialog", dialog)
        self.assertIn("api.updateCharacter", dialog)
        self.assertIn("maestro-characters-changed", dialog)
        # Replacing media must never invent a second record.
        self.assertIn("keeps its identity", dialog)

    def test_the_reference_picker_offers_edit_in_its_options_menu(self):
        picker = _read(CHARACTERS / "ReferenceCharacterPicker.tsx")

        self.assertIn("CharacterEditDialog", picker)
        self.assertIn("Edit picture, voice or name", picker)
        # The menu already carried export and delete; editing belongs beside
        # them so a wrong picture does not require delete-and-reupload.
        self.assertIn("ExportCharacterButton", picker)
        self.assertIn("Delete", picker)

    def test_the_character_browser_and_tts_library_use_the_same_dialog(self):
        browser = _read(CHARACTERS / "CharacterBrowser.tsx")
        tts = _read(SIDEBAR / "TtsCharacterLibrary.tsx")

        for source in (browser, tts):
            self.assertIn("CharacterEditDialog", source)
            self.assertIn("setEditing", source)
        self.assertIn("Edit", browser)
        self.assertIn("Edit", tts)

    def test_no_component_keeps_a_second_inline_edit_form(self):
        """Two implementations would drift; the dialog is the only one."""

        for path in CHARACTERS.glob("*.tsx"):
            source = _read(path)
            if path.name == "CharacterEditDialog.tsx":
                continue
            self.assertNotIn(
                "Replace the picture or video",
                source,
                f"{path.name} re-implements the character edit form",
            )


if __name__ == "__main__":
    unittest.main()
