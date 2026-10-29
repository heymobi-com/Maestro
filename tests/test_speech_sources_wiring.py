"""Document grounding is wired end to end, and its only dependency stays optional.

The retrieval engine is tested in ``test_sources_index.py``. These cases pin the
plumbing: the endpoints exist, the panel is reachable, the write action searches
with the brief *before* writing, and the one new dependency cannot break the app.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH = APP / "launch.py"
SERVICE = APP / "services/sources_index.py"
CLIENT = ROOT / "ui/src/api/client.ts"
PROMPT_INPUT = ROOT / "ui/src/components/Sidebar/PromptInput.tsx"
PANEL = ROOT / "ui/src/components/Sidebar/SourcesPanel.tsx"
AUDIO_SECTION = ROOT / "ui/src/components/Sidebar/AudioModeSection.tsx"
REQUIREMENTS = APP / "requirements.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class EndpointTests(unittest.TestCase):
    def test_the_five_source_routes_exist(self):
        launch = _read(LAUNCH)

        for route in (
            '@api.post("/api/v1/sources/upload")',
            '@api.post("/api/v1/sources/ingest")',
            '@api.post("/api/v1/sources/ingest-folder")',
            '@api.get("/api/v1/sources")',
            '@api.post("/api/v1/sources/search")',
            '@api.post("/api/v1/sources/reset")',
        ):
            with self.subTest(route=route):
                self.assertIn(route, launch)

    def test_ingest_is_bounded_to_uploads_and_the_workspace(self):
        launch = _read(LAUNCH)
        self.assertIn('detail=f"Source: path not allowed: {raw}"', launch)
        # A folder obeys the same boundary as a single file.
        self.assertIn('detail=f"Folder: path not allowed: {raw}"', launch)
        # Indexing runs off the event loop: a 500-page PDF must not freeze the app.
        self.assertIn("await asyncio.to_thread(\n        sources_index.build", launch)

    def test_the_search_answer_carries_document_citations(self):
        launch = _read(LAUNCH)
        self.assertIn('"citations": sources_index.citation_rows(results),', launch)


class CitationReviewTests(unittest.TestCase):
    """Citations are reviewed before the audio exists, and recorded in the MP3."""

    REVIEW = ROOT / "ui/src/components/Sidebar/SpeechCitationReview.tsx"
    MASTER = ROOT / "ui/src/components/Sidebar/PodcastMasterDialog.tsx"

    def test_the_review_opens_only_after_a_grounded_write(self):
        prompt_input = _read(PROMPT_INPUT)
        review = _read(self.REVIEW)

        self.assertIn("setCitationReview({ citations, passages })", prompt_input)
        # It must happen after the writer returns: there is nothing to cite before.
        self.assertLess(
            prompt_input.index("await enhancePrompt("),
            prompt_input.index("setCitationReview({ citations, passages })"),
        )
        self.assertIn("The script was written from these documents", review)

    def test_the_default_view_is_the_document_and_passages_are_collapsed(self):
        review = _read(self.REVIEW)

        self.assertIn("const [showPassages, setShowPassages] = useState(false)", review)
        self.assertIn("passages used", review)
        # Unchecking a document removes it from the citations that travel on.
        self.assertIn("citation.source_name] !== false", review)
        self.assertIn("setParam('_speech_citations', kept)", _read(PROMPT_INPUT))

    def test_the_master_cites_the_reviewed_documents(self):
        master = _read(self.MASTER)
        launch = _read(LAUNCH)

        self.assertIn("citations: useStore.getState().params._speech_citations,", master)
        # The file itself names them: a listener cannot hear a passage reference.
        self.assertIn('metadata_body["comment"] = "Sources: "', launch)
        self.assertIn('"citations": citations,', launch)

    def test_both_inputs_are_offered_in_the_panel(self):
        panel = _read(PANEL)

        self.assertIn("const addFolder = async (files: FileList | null)", panel)
        self.assertIn("webkitdirectory: ''", panel)
        self.assertIn("file(s) ignored: unsupported type", panel)


class OptionalDependencyTests(unittest.TestCase):
    def test_pypdf_is_pinned_and_its_reason_recorded(self):
        requirements = _read(REQUIREMENTS)

        self.assertIn("pypdf==5.1.0", requirements)
        self.assertIn("no compiled dependency", requirements)

    def test_the_pdf_reader_is_imported_lazily_and_degrades(self):
        service = _read(SERVICE)

        # A module-level import would break the whole app where pypdf is absent.
        top = service.split("def _read_pdf_file", 1)[0]
        self.assertNotIn("import PdfReader", top)
        self.assertIn("from pypdf import PdfReader", service)
        self.assertIn("PDF reading needs the 'pypdf' package", service)

    def test_the_common_formats_need_no_dependency_at_all(self):
        service = _read(SERVICE)

        for module in ("zipfile", "xml.etree", "html.parser"):
            with self.subTest(module=module):
                self.assertIn(module, service)
        # lxml would have to be pinned into a 74-pin manifest; DOCX avoids it.
        self.assertNotIn("import lxml", service)
        self.assertNotIn("from docx", service)


class GroundingWiringTests(unittest.TestCase):
    def test_the_write_action_searches_with_the_brief_first(self):
        prompt_input = _read(PROMPT_INPUT)

        self.assertIn("const found = await api.searchSources({", prompt_input)
        self.assertIn("query: brief,", prompt_input)
        # Retrieval happens before the writer call, and its block joins the brief.
        self.assertLess(
            prompt_input.index("api.searchSources"),
            prompt_input.index("await enhancePrompt("),
        )
        self.assertIn("grounding ? `${written}\\n\\n${grounding}` : written", prompt_input)

    def test_a_failed_search_does_not_stop_the_write(self):
        prompt_input = _read(PROMPT_INPUT)
        self.assertIn("// Sources are optional: a failure here must not stop the write.", prompt_input)

    def test_the_panel_is_reachable_from_the_speech_panel(self):
        panel = _read(PANEL)
        section = _read(AUDIO_SECTION)

        self.assertIn("export function SourcesPanel", panel)
        self.assertIn("export function SourcesButton", panel)
        self.assertIn("accept=\".md,.markdown,.txt,.text,.rst,.html,.htm,.xhtml,.docx,.pdf\"", panel)
        self.assertIn("{isAudioOnly && <SourcesButton />}", section)

    def test_the_client_exposes_every_source_call(self):
        client = _read(CLIENT)
        for name in ("uploadSources", "ingestSources", "fetchSources", "searchSources", "resetSources"):
            with self.subTest(name=name):
                self.assertIn(f"export async function {name}", client)


if __name__ == "__main__":
    unittest.main()
