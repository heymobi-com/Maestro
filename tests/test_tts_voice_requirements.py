"""A voice slot with no reference is refused before it reaches the engine.

Measured on this install, a two-slot podcast written by ``Write Podcast`` and sent
without references failed twice, after queueing and as a skipped task:

    Multi-voice cloning requires at least two reference audio files.   (KugelAudio)
    Qwen3 Base requires Speaker 1 reference audio.                     (Qwen3 Base)

Both messages name the model's rule, not the thing the user has to do, and both
cost a queue round-trip. The UI now refuses the request with the slot number, and
the podcast writer asks for labels the engines can bind to a voice.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TTS_VOICES = ROOT / "ui/src/lib/ttsVoices.ts"
STORE = ROOT / "ui/src/stores/useStore.ts"
PODCAST_WRITER = ROOT / "ui/src/lib/podcastWriter.ts"
PROMPT_INPUT = ROOT / "ui/src/components/Sidebar/PromptInput.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class MissingVoiceReferenceTests(unittest.TestCase):
    def test_the_helper_reports_the_slot_numbers(self):
        source = _read(TTS_VOICES)

        self.assertIn("export function ttsMissingVoiceReferences", source)
        self.assertIn("const limit = ttsVoiceLimit(options)", source)
        self.assertIn("if (!voices[index]?.path) missing.push(index + 1)", source)
        # A model that declares no voice-reference slots needs none.
        self.assertIn("if (limit <= 0) return []", source)
        # Only the slots the model will actually receive are checked.
        self.assertIn("const active = Math.min(count, limit)", source)

    def test_generation_is_refused_with_an_actionable_message(self):
        store = _read(STORE)

        self.assertIn("const missing = ttsMissingVoiceReferences(", store)
        self.assertIn("if (missing.length > 0) {", store)
        self.assertIn("has no reference audio", store)
        self.assertIn("Pick a saved ", store)
        # It must come back before a job is submitted, like the other preflight
        # refusals in this function.
        guard = store.index("const missing = ttsMissingVoiceReferences(")
        self.assertLess(guard, store.index("await api.submitGeneration(params, holdForQueue)"))


class PodcastLabelTests(unittest.TestCase):
    def test_the_writer_asks_for_labels_the_engines_can_bind(self):
        writer = _read(PODCAST_WRITER)
        instructions = [
            line.strip() for line in writer.splitlines() if line.strip().startswith("'")
        ]

        self.assertTrue(any("Speaker 1: or Speaker 2:" in line for line in instructions))
        # "HOST A:" is not a voice slot: every line would be read by the first
        # speaker, which is the defect this instruction used to cause. Only the
        # spoken instructions matter; the comment above them explains the rule.
        for line in instructions:
            self.assertNotIn("HOST A", line)

    def test_segment_headings_are_kept_out_of_the_spoken_lines(self):
        # A measured draft opened with "Segmento uno: La ciencia detrás de esa
        # aspereza", which the engine reads out loud as part of the episode.
        writer = _read(PODCAST_WRITER)
        self.assertIn("Never announce segment numbers", writer)

    def test_saved_character_names_still_bind_a_written_label(self):
        """`Bela:` must reach the slot whose character is saved as `Bela (S1)`.

        Matching raw names left the label unchanged, and an unrecognised label is
        read by the first voice, so a two-host episode came out in one voice.
        """
        voices = _read(TTS_VOICES)

        self.assertIn("export function normalizeTtsSpeakerName", voices)
        self.assertIn(".replace(/\\([^)]*\\)/g, ' ')", voices)
        self.assertIn("names.get(normalizeTtsSpeakerName(name))", voices)
        # The character name is tried too: a slot may store a display name.
        self.assertIn("for (const candidate of [voice.name, voice.characterName])", voices)

    def test_an_empty_brief_is_refused_instead_of_invented(self):
        prompt_input = _read(PROMPT_INPUT)

        self.assertIn("if (!brief.trim()) {", prompt_input)
        self.assertIn("Write what this is about in the prompt box first", prompt_input)
        self.assertIn("write your topic in the box first", prompt_input)


class PodcastLengthTests(unittest.TestCase):
    """A podcast brief needs a length, or the writer answers with a trailer.

    Measured: a brief with the right topic and no length produced 111 words, about
    fifty seconds. The writer only sizes a script from an explicit length, and until
    now every pattern required it attached to a medium ("10 minutes of dialogue"),
    so the natural way to ask - "unos 8 minutos" - set no target at all.
    """

    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT / "app"))
        from services import speech_writer
        self.speech_writer = speech_writer

    def test_a_bare_length_in_either_language_sizes_the_script(self):
        resolve = self.speech_writer.resolve_speech_target

        for brief, minutes in (
            ("Un episodio sobre voces. Unos 8 minutos.", 8),
            ("A podcast about voices. About 10 minutes.", 10),
            ("Un podcast de tres minutos.", 3),
            ("20 minutos, sobre TTS", 20),
        ):
            with self.subTest(brief=brief):
                self.assertEqual(resolve(brief).minutes, minutes)

    def test_narrative_time_is_not_a_length_request(self):
        # The existing suite already pinned this sentence; the bare pattern broke it
        # and made the writer plan a ten-minute script.
        target = self.speech_writer.resolve_speech_target(
            "Write a story about two travelers who waited ten minutes for a train."
        )
        self.assertEqual(target.words, 0)

    def test_a_spanish_script_size_is_understood(self):
        self.assertEqual(
            self.speech_writer.resolve_speech_target("1500 palabras").words, 1500,
        )

    def test_the_ui_imposes_eight_minutes_only_when_the_brief_states_none(self):
        writer = _read(PODCAST_WRITER)

        self.assertIn("export const PODCAST_DEFAULT_MINUTES = 8", writer)
        self.assertIn("export function briefStatesDuration", writer)
        self.assertIn("export function podcastTargetMinutes", writer)
        # Spanish units and words, or a Spanish brief would never match.
        self.assertIn("minutos?", writer)
        self.assertIn("dos|tres", writer)
        # A bare length counts only at the end of a clause.
        self.assertIn("DURATION_CLAUSE_END", writer)

    def test_the_default_travels_with_the_call_instead_of_being_saved(self):
        prompt_input = _read(PROMPT_INPUT)
        store = _read(STORE)

        # Every spoken format supplies its own default length per call.
        self.assertIn("briefStatesDuration(brief) ? undefined : spec.minutes", prompt_input)
        # enhancePrompt gained an optional argument, so ordinary speech writing keeps
        # its own length and the Advanced override is never silently rewritten.
        self.assertIn("speechTargetMinutes?: number", store)
        self.assertIn("tts_target_minutes: speechTargetMinutes ?? params._speech_writer_target_minutes ?? 0", store)


class DeliverySyntaxTests(unittest.TestCase):
    """The writer must speak each engine's own syntax, not one shared dialect.

    Fish Drama 3's own dialogue guide asks for ``[whisper]`` / ``<emphasis>``
    marks. KugelAudio and Qwen3's guide forbids brackets and emotion tags
    outright ("NO brackets, NO emotion tags like [tired]"). A single brief either
    suppresses the tags Fish needs or injects tags Kugel will read aloud.
    """

    def test_fish_is_told_to_use_its_marks(self):
        writer = _read(PODCAST_WRITER)
        prompt_input = _read(PROMPT_INPUT)

        self.assertIn("export type PodcastTagStyle = 'fish' | 'plain'", writer)
        self.assertIn("export function podcastTagStyle", writer)
        self.assertIn("/^fish/i", writer)
        self.assertIn("[whisper]", writer)
        self.assertIn("<emphasis>", writer)
        # The selected model decides, so the call site passes its architecture.
        self.assertIn("podcastTagStyle(modelOptions?.architecture)", prompt_input)

    def test_kugel_and_qwen_are_told_that_markup_is_read_aloud(self):
        writer = _read(PODCAST_WRITER)

        self.assertIn("accepts no brackets, no emotion tags", writer)
        self.assertIn("the pauses", writer)

    def test_no_instruction_forbids_every_tag(self):
        """The old line suppressed Fish's own delivery cues."""

        writer = _read(PODCAST_WRITER)
        self.assertNotIn(
            "Do not put stage directions, camera notes, or production instructions",
            writer,
        )
        # Camera and scene directions stay banned for every engine.
        self.assertIn("no camera or scene directions", writer)

    def test_the_brief_still_carries_the_episode_shape_either_way(self):
        writer = _read(PODCAST_WRITER)
        self.assertIn("export function podcastWriterBrief", writer)
        for line in ("cold open", "two to four clear segments", "recap and an outro"):
            with self.subTest(line=line):
                self.assertIn(line, writer)


if __name__ == "__main__":
    unittest.main()
