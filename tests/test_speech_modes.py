"""Each spoken format is its own script shape, not a podcast with a new label.

A documentary, a narrated story and an audio drama differ in structure (hook vs.
cold open vs. conflict already in progress), in who speaks, and in what holds a
listener. They also differ in whether a speaker label belongs in the script at all:
narration must not carry one, or the engine reads "Speaker 1:" out loud.

The delivery syntax stays the engine's (see test_tts_voice_requirements.py), so
these cases only pin the format side.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = ROOT / "ui/src/lib/speechModes.ts"
PROMPT_INPUT = ROOT / "ui/src/components/Sidebar/PromptInput.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FormatCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.modes = _read(MODES)

    def test_the_five_formats_exist(self):
        for mode in ("podcast", "interview", "documentary", "story", "drama"):
            with self.subTest(mode=mode):
                self.assertIn(f"id: '{mode}'", self.modes)

    def test_each_format_states_whether_a_label_belongs_in_the_script(self):
        self.assertIn("export type SpeechVoiceShape = 'dialogue' | 'narration'", self.modes)
        # Narration formats must be told to write without speaker labels.
        self.assertIn("Write continuous narration without speaker labels", self.modes)
        # Dialogue formats must be told which label to use.
        self.assertIn("Label every spoken line with Speaker 1:", self.modes)

    def test_narration_uses_one_voice_and_the_monologue_guide(self):
        self.assertIn("export function speechModeWriterMode", self.modes)
        self.assertIn("return speechModeSpec(mode).shape === 'dialogue' ? 'dialogue' : 'monologue'", self.modes)
        self.assertIn("return speechModeSpec(mode).shape === 'dialogue' ? 2 : 1", self.modes)

    def test_each_format_brings_a_retention_device_rather_than_a_topic(self):
        """Structure is what makes a long script hold: hooks, acts, reveals."""

        for phrase in (
            "never open with a definition",                    # documentary
            "one concrete want and one concrete obstacle",     # story
            "in the middle of a conflict already in progress",  # drama
            "the single question the whole interview is trying to answer",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.modes)

    def test_a_length_default_exists_per_format(self):
        self.assertRegex(self.modes, r"minutes: \d+")
        # The menu passes it only when the brief states no length.
        prompt_input = _read(PROMPT_INPUT)
        self.assertIn("briefStatesDuration(brief) ? undefined : spec.minutes", prompt_input)


class MenuWiringTests(unittest.TestCase):
    def test_the_menu_offers_every_format_and_sets_its_writer_mode(self):
        prompt_input = _read(PROMPT_INPUT)

        self.assertIn("SPEECH_MODES.filter(format => format.shape === 'narration')", prompt_input)
        self.assertIn("SPEECH_MODES.filter(format => format.shape === 'dialogue')", prompt_input)
        self.assertIn("onClick={() => writeSpeechFormat(format.id)}", prompt_input)
        self.assertIn("speechModeWriterMode(format)", prompt_input)
        # The engine still decides the tags.
        self.assertIn("podcastTagStyle(modelOptions?.architecture)", prompt_input)

    def test_an_empty_brief_still_names_the_format_that_was_chosen(self):
        prompt_input = _read(PROMPT_INPUT)

        self.assertIn("${spec.label} expands that brief.", prompt_input)

    def test_off_screen_formats_do_not_claim_a_camera(self):
        prompt_input = _read(PROMPT_INPUT)
        modes = _read(MODES)

        self.assertIn("Never describe what a camera would see", modes)
        # The old podcast-only entry point is gone from the menu wiring.
        self.assertNotIn("const writePodcast = ", prompt_input)


if __name__ == "__main__":
    unittest.main()
