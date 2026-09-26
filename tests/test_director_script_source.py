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
# The written-script source is ours, so its state and patches live outside the
# store: see ui/src/stores/directorSlice.ts.
SLICE = ROOT / "ui/src/stores/directorScriptSlice.ts"
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
        slice_source = _read(SLICE)

        self.assertIn(
            "source === 'script' && state.directorStep === 'upload' ? { directorStep: 'style' as const } : {}",
            slice_source,
        )
        # The timeline and the transcript are derived when the script changes, so the
        # review steps show the clips the render will use.
        self.assertIn("const parsed = parseDirectorScript(text)", slice_source)
        self.assertIn("directorScriptClips: parsed.clips", slice_source)
        # The store only delegates, so its half of the seam stays one line.
        self.assertIn(
            "setDirectorScriptText: (text) => set(directorScriptTextPatch(text))",
            _read(STORE),
        )


class RequestRoutingTests(unittest.TestCase):
    def test_the_plan_request_uses_the_script_timeline_and_lines(self):
        store = _read(STORE)
        slice_source = _read(SLICE)

        # The store reads the script state and hands it to the planner...
        self.assertIn(
            "const directorPlannedClips = directorScriptTimeline(get(), get().directorPlannedClips)",
            store,
        )
        self.assertIn("...directorScriptPlanFields(get(), directorAnalysis?.lyrics),", store)
        # ...and the decision itself lives in our own module.
        self.assertIn("return directorScriptIsActive(state) ? state.directorScriptClips : analysed", slice_source)
        # The authored rows are the source document too, so the H3 ledger locks the
        # written lines and cannot accept a paraphrase instead.
        self.assertIn("lyrics: state.directorScriptTranscript,", slice_source)
        self.assertIn("story_description: state.directorScriptText,", slice_source)

    def test_the_generation_request_drops_the_missing_soundtrack(self):
        store = _read(STORE)
        slice_source = _read(SLICE)

        self.assertIn("const scriptFields = directorScriptPipelineFields(", store)
        # The script owns the soundtrack, the timeline and the words, so all of its
        # fields travel as one spread instead of being re-listed by hand.
        self.assertIn("...scriptFields,", store)
        self.assertIn("audio_path: undefined,", slice_source)
        self.assertIn("planned_clips: state.directorScriptClips,", slice_source)

    def test_the_render_is_told_that_the_dialogue_was_written(self):
        """A script project has no soundtrack, and the H3 Omni path refuses one.

        Measured on viral-2: the authored rows reached the pipeline as a transcript
        with no audio_path, and the render died before it started asking for "the
        uploaded soundtrack or dialogue audio". The exemption already existed for
        short_film_story, so a written dialogue travels with an explicit marker.
        """
        slice_source = _read(SLICE)
        store = _read(STORE)
        pipeline = _read(PIPELINE)

        # The UI marks the request...
        self.assertIn("_director_script_source: true,", slice_source)
        self.assertIn("const scriptFields = directorScriptPipelineFields(", store)
        # ...and the fields must actually travel: the request used to list
        # audio_path/planned_clips/lyrics one by one and silently leave the marker
        # behind, so a real run died asking for a soundtrack that never existed.
        # A test that pins both ends but not the wire between them pins nothing.
        self.assertIn("...scriptFields,", store)
        self.assertNotIn("audio_path: scriptFields.audio_path,", store)
        # ...and the pipeline honours exactly that marker.
        self.assertIn('params.get("_director_script_source")', pipeline)
        self.assertIn('needs_uploaded_audio = (', pipeline)
        self.assertIn('pipeline_type != "short_film_story"', pipeline)

    def test_the_pipeline_never_hands_the_planner_a_string_transcript(self):
        pipeline = _read(PIPELINE)
        # Iterating a string transcript yields single characters, and the planner
        # calls .get() on each one.
        self.assertIn('written = params.get("transcript")', pipeline)
        self.assertIn("if not isinstance(written, list):", pipeline)
        self.assertIn("written = analysed if isinstance(analysed, list) else None", pipeline)


class ScriptReferenceTests(unittest.TestCase):
    """A written script supplies the words, not the faces.

    Hiding the upload step for a script also hid the reference inputs that lived inside
    it, so a script project had no way to add its characters. The same inputs the audio
    flow shows are now rendered on the style step, exactly as the story path does.
    """

    def setUp(self):
        self.chat = _read(CHAT)

    def test_a_script_project_can_still_add_its_references(self):
        self.assertIn("const scriptMode = scriptCapable && scriptSource === 'script'", self.chat)
        start = self.chat.index("{scriptMode && atStep('style') && (")
        block = self.chat[start:start + 1500]
        # DirectorReferenceInputs resolves the ordered H3 Omni references, or the
        # reference photo plus the character and location rows.
        self.assertIn("<DirectorReferenceInputs", block)
        self.assertIn("referenceImage={referenceImage}", block)
        self.assertIn("<CharacterNaming", block)

    def test_the_references_are_summarised_once_the_style_step_passes(self):
        self.assertIn("{scriptMode && pastStep('style') && (referenceImage || directorUsesOmniManifest) && (", self.chat)

    def test_the_audio_zone_stays_out_of_a_script_project(self):
        self.assertIn(
            "{skill && (!isShortFilm || shortFilmPath === 'audio') && scriptSource !== 'script'",
            self.chat,
        )


if __name__ == "__main__":
    unittest.main()
