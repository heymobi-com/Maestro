"""A written script is a first-class source for the skills that read a transcript.

A Director project normally begins by uploading audio: the analysis produces the
clip timeline and the transcript. Podcast and Viral Video plan from a transcript,
so they can equally plan from a script the user writes -- no audio pass, and H3
generates the voices for the authored lines.

These tests pin the whole path: the parser, the source choice, the plan request,
the generation request and the pipeline's tolerance of a structured transcript.
They also pin the invariant that matters most for a Spanish project: nothing here
invents a language for an untagged row, because tagging Spanish words as English
is what makes the model read them with English phonetics.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "ui/src/lib/directorScript.ts"
CHAT = ROOT / "ui/src/components/Sidebar/DirectorChat.tsx"
STORE = ROOT / "ui/src/stores/useStore.ts"
PIPELINE = ROOT / "app/services/director_pipeline.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ScriptParserTests(unittest.TestCase):
    def test_speaker_rows_and_delivery_are_recognised(self):
        parser = _read(PARSER)
        self.assertIn("_SPEAKER_ROW", parser)
        self.assertIn("delivery", parser)
        # The forms the H3 story ledger already locks: NAME: line and NAME (tone): line.
        self.assertIn(r"\(([^)\n]{0,40})\)", parser)
        self.assertIn("clips: ScriptClip[]", parser)
        self.assertIn("transcript: ScriptTranscriptRow[]", parser)

    def test_the_transcript_rows_carry_what_the_planner_filters_on(self):
        parser = _read(PARSER)
        # The podcast planner keeps a line in a clip when
        # ``row.start < clip.end and row.end > clip.start``: a row without `end`
        # silently drops every line it was written to carry.
        self.assertIn("start: number", parser)
        self.assertIn("end: number", parser)
        self.assertIn("speaker: string", parser)
        self.assertIn("text: string", parser)

    def test_no_language_is_invented_for_an_untagged_row(self):
        parser = _read(PARSER)
        self.assertNotIn("'English'", parser)
        self.assertNotIn('"English"', parser)
        # Only a declared tag is reported, and an utterance keeps the authored words.
        self.assertIn("scriptTurnLanguage", parser)
        self.assertIn("scriptUtterance", parser)


class SourceChoiceTests(unittest.TestCase):
    def test_the_chooser_is_offered_only_where_a_script_planner_exists(self):
        chat = _read(CHAT)
        self.assertIn("const scriptCapable = skill === 'podcast' || skill === 'viral_video'", chat)
        self.assertIn("<ScriptSourceChooser onSelect={setScriptSource} />", chat)
        self.assertIn("label: 'Write a Script'", chat)

    def test_the_script_editor_writes_through_to_the_store(self):
        chat = _read(CHAT)
        self.assertIn("onChange={event => setScriptText(event.target.value)}", chat)
        self.assertIn("value={scriptText}", chat)
        # The editor names the two forms the user has to use.
        self.assertIn("NAME: line", chat)
        self.assertIn("&lt;d&gt;[Spanish] ...&lt;/d&gt;", chat)

    def test_the_upload_zone_steps_aside_for_a_written_script(self):
        chat = _read(CHAT)
        self.assertIn("scriptSource !== 'script' && (atStep('upload')", chat)

    def test_the_step_advances_without_an_audio_pass(self):
        store = _read(STORE)
        self.assertIn(
            "source === 'script' && state.directorStep === 'upload' ? { directorStep: 'style' as const } : {}",
            store,
        )
        # The timeline and the transcript are derived when the script changes, so the
        # review steps show the clips the render will use.
        self.assertIn("const parsed = parseDirectorScript(text)", store)
        self.assertIn("directorScriptClips: parsed.clips", store)


class RequestRoutingTests(unittest.TestCase):
    def test_the_plan_request_uses_the_script_timeline_and_lines(self):
        store = _read(STORE)
        self.assertIn(
            "const scriptMode = get().directorScriptSource === 'script' && get().directorScriptClips.length > 0",
            store,
        )
        self.assertIn("const directorPlannedClips = scriptMode ? get().directorScriptClips : get().directorPlannedClips", store)
        self.assertIn("lyrics: scriptMode ? get().directorScriptTranscript : (directorAnalysis?.lyrics ?? undefined)", store)
        # The authored rows are the source document too, so the H3 ledger locks the
        # written lines and cannot accept a paraphrase instead.
        self.assertIn("...(scriptMode ? { story_description: get().directorScriptText } : {})", store)

    def test_the_generation_request_drops_the_missing_soundtrack(self):
        store = _read(STORE)
        self.assertIn("let pipelineType = 'music_video'", store)
        self.assertIn(
            "audio_path: state.directorScriptSource === 'script' && state.directorScriptClips.length > 0",
            store,
        )
        self.assertIn(
            "planned_clips: state.directorScriptSource === 'script' && state.directorScriptClips.length > 0",
            store,
        )

    def test_the_pipeline_never_hands_the_planner_a_string_transcript(self):
        pipeline = _read(PIPELINE)
        # Iterating a string transcript yields single characters, and the planner
        # calls .get() on each one.
        self.assertIn('written = params.get("transcript")', pipeline)
        self.assertIn("if not isinstance(written, list):", pipeline)
        self.assertIn("written = analysed if isinstance(analysed, list) else None", pipeline)


if __name__ == "__main__":
    unittest.main()
