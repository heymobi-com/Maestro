"""Documents become passages a writer can quote, without a model or a new toolchain.

The measured complaint behind this module: a one-line brief produced a two-minute
script of improvisation. Retrieval is the part that gives a long script material.

These cases pin the three decisions that matter:

* **no new dependency for the common formats** — a DOCX is read with ``zipfile``
  and ``xml.etree``, which keeps ``lxml`` out of a 74-pin manifest;
* **retrieval is lexical and deterministic**, so it can be tested without a model;
* **untrusted input is bounded** — a bad file is reported, never raised.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services import sources_index as si  # noqa: E402


def _write(folder: Path, name: str, content: str | bytes) -> Path:
    path = folder / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _docx_bytes(paragraphs: list[str]) -> bytes:
    """A minimal but valid DOCX, built with the standard library."""

    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_markdown_and_text_are_read(self):
        path = _write(self.folder, "notes.md", "# Título\n\nTexto con datos: 42%.")
        self.assertIn("42%", si.extract_text(str(path)))

    def test_html_drops_script_and_style_but_keeps_prose(self):
        path = _write(self.folder, "page.html", (
            "<html><head><style>.x{color:red}</style></head><body>"
            "<script>var secret = 1;</script><h1>Informe</h1><p>El coste fue 12 €.</p>"
            "</body></html>"
        ))
        text = si.extract_text(str(path))

        self.assertIn("El coste fue 12", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("color:red", text)

    def test_docx_is_read_without_python_docx(self):
        path = _write(self.folder, "informe.docx", _docx_bytes([
            "Primer párrafo con una cifra: 3,5 millones.",
            "Segundo párrafo.",
        ]))
        text = si.extract_text(str(path))

        self.assertIn("3,5 millones", text)
        self.assertIn("Segundo párrafo", text)
        # The point of the stdlib path: no lxml anywhere.
        self.assertNotIn("lxml", sys.modules)

    def test_a_broken_docx_is_reported_not_raised(self):
        path = _write(self.folder, "roto.docx", b"not a zip at all")

        with self.assertRaises(si.UnreadableSource) as caught:
            si.extract_text(str(path))
        self.assertIn("not a valid .docx", str(caught.exception))

    def test_an_unsupported_extension_names_the_accepted_ones(self):
        path = _write(self.folder, "hoja.xlsx", b"PK\x03\x04")

        with self.assertRaises(si.UnreadableSource) as caught:
            si.extract_text(str(path))
        self.assertIn("Markdown, TXT, HTML, DOCX or PDF", str(caught.exception))

    def test_a_missing_file_is_a_report_not_a_crash(self):
        with self.assertRaises(si.UnreadableSource):
            si.extract_text(str(self.folder / "no-existe.md"))


class ChunkingTests(unittest.TestCase):
    def test_passages_split_on_paragraphs_and_carry_their_heading(self):
        text = (
            "# Capítulo 1\n\n"
            + ("Frase de relleno sobre compresión. " * 12)
            + "\n\n## Detalle técnico\n\n"
            + ("Otra frase sobre el códec. " * 12)
        )
        chunks = si.chunk_text(text, source_id="s1", source_name="informe.md")

        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn("Capítulo 1", {chunk.heading for chunk in chunks})
        self.assertIn("Detalle técnico", {chunk.heading for chunk in chunks})
        for chunk in chunks:
            self.assertEqual(chunk.source_id, "s1")
            self.assertLessEqual(len(chunk.text), si.MAX_CHUNK_CHARS + 200)

    def test_empty_text_yields_nothing(self):
        self.assertEqual(si.chunk_text("   \n\n  "), [])


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.index = si.SourceIndex()
        self.index.add_source({"id": "a", "name": "codec.md"}, (
            "# Códec de audio\n\n"
            "El códec trabaja a 32 kHz y suena metálico por falta de armónicos.\n\n"
            "# Voces\n\n"
            "La clonación de voz necesita entre dos y treinta segundos de habla clara."
        ))
        self.index.add_source({"id": "b", "name": "negocio.md"}, (
            "# Costes\n\n"
            "La suscripción en la nube cuesta quince euros al mes por usuario."
        ))

    def test_the_passage_that_answers_the_question_comes_first(self):
        results = self.index.search("¿por qué suena metálico el códec?", limit=3)

        self.assertTrue(results)
        self.assertIn("metálico", results[0]["text"])
        self.assertEqual(results[0]["source_name"], "codec.md")

    def test_a_question_about_another_document_finds_it(self):
        results = self.index.search("cuánto cuesta la suscripción", limit=2)

        self.assertTrue(results)
        self.assertEqual(results[0]["source_name"], "negocio.md")

    def test_a_query_with_no_material_returns_nothing(self):
        # The writer must be able to tell "not covered" from "empty answer".
        self.assertEqual(self.index.search("fotosíntesis en Marte"), [])

    def test_a_brief_typed_without_accents_still_matches(self):
        results = self.index.search("por que suena metalico el codec")

        self.assertTrue(results)
        self.assertIn("armónicos", results[0]["text"])

    def test_an_inflected_form_finds_its_own_passage(self):
        # Measured: "clonar" and "clonación" did not match, so a question about
        # cloning ranked the pricing passage first.
        results = self.index.search("cuánto audio necesito para clonar una voz", limit=1)

        self.assertTrue(results)
        self.assertEqual(results[0]["heading"], "Voces")

    def test_the_index_survives_a_round_trip_to_disk(self):
        folder = Path(tempfile.mkdtemp())
        si.save_index(str(folder), self.index)
        reloaded = si.load_index(str(folder))

        self.assertEqual(len(reloaded.chunks), len(self.index.chunks))
        self.assertEqual(reloaded.sources, self.index.sources)
        self.assertIn("metálico", reloaded.search("suena metálico")[0]["text"])

    def test_a_corrupt_index_file_is_ignored(self):
        folder = Path(tempfile.mkdtemp())
        (folder / si.INDEX_FILENAME).write_text("{not json", encoding="utf-8")

        self.assertEqual(si.load_index(str(folder)).chunks, [])


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.index = si.SourceIndex()
        self.index.add_source({"id": "a", "name": "codec.md"}, (
            "# Códec\n\nEl códec trabaja a 32 kHz. La voz suena metálica.\n\n"
            "# Voces\n\nLa clonación necesita dos segundos de habla clara."
        ))

    def test_the_block_tells_the_writer_not_to_invent(self):
        results = self.index.search("códec metálico")
        block = si.context_block(results)

        self.assertIn("REFERENCE MATERIAL", block)
        self.assertIn("do not add facts", block)
        self.assertIn("never invent it", block)
        self.assertIn("32 kHz", block)

    def test_no_results_means_no_block(self):
        # An empty block would silently read as "no material", which is honest.
        self.assertEqual(si.context_block(self.index.search("termodinámica")), "")

    def test_the_block_stays_within_its_budget(self):
        huge = [{"text": "palabra " * 2000, "heading": "H", "source_name": "s"}]
        block = si.context_block(huge, max_chars=500)

        self.assertLess(len(block), 700)


class BuildTests(unittest.TestCase):
    def test_a_bad_document_does_not_lose_the_good_ones(self):
        folder = Path(tempfile.mkdtemp())
        good = _write(folder, "bueno.md", "# Datos\n\n" + "Cifra concreta 42. " * 20)
        bad = _write(folder, "malo.docx", b"broken")

        index, report = si.build([
            {"path": str(good), "name": "bueno.md"},
            {"path": str(bad), "name": "malo.docx"},
        ])

        self.assertGreater(len(index.chunks), 0)
        statuses = {row["name"]: row["status"] for row in report}
        self.assertEqual(statuses["bueno.md"], "indexed")
        self.assertIn("not a valid .docx", statuses["malo.docx"])

    def test_indexing_the_same_file_twice_is_not_duplicated(self):
        folder = Path(tempfile.mkdtemp())
        path = _write(folder, "uno.md", "# A\n\n" + "Texto repetido. " * 20)
        index, _ = si.build([{"path": str(path), "name": "uno.md"}])
        count = len(index.chunks)

        index, report = si.build(
            [{"path": str(path), "name": "uno.md"}], existing=index,
        )

        self.assertEqual(len(index.chunks), count)
        self.assertEqual(report[0]["status"], "already indexed")


class FolderTests(unittest.TestCase):
    def test_a_folder_yields_its_supported_documents_and_names_the_skipped(self):
        folder = Path(tempfile.mkdtemp())
        _write(folder, "uno.md", "# A\n\n" + "Texto uno. " * 30)
        (folder / "sub").mkdir()
        _write(folder / "sub", "dos.txt", "# B\n\n" + "Texto dos. " * 30)
        _write(folder, "hoja.xlsx", b"PK\x03\x04")

        documents, skipped = si.folder_documents(str(folder))

        names = {row["name"] for row in documents}
        self.assertEqual(names, {"uno.md", os.path.join("sub", "dos.txt")})
        self.assertEqual([row["status"] for row in skipped], ["unsupported type"])

    def test_a_folder_can_be_ingested_with_one_call(self):
        folder = Path(tempfile.mkdtemp())
        _write(folder, "uno.md", "# Datos\n\n" + "Cifra 42. " * 40)
        _write(folder, "dos.txt", "# Otros\n\n" + "Dato 7. " * 40)

        documents, _ = si.folder_documents(str(folder))
        index, report = si.build(documents)

        self.assertEqual(len(index.sources), 2)
        self.assertTrue(all(row["status"] == "indexed" for row in report))
        self.assertGreater(len(index.chunks), 0)

    def test_the_document_limit_is_enforced_and_reported(self):
        folder = Path(tempfile.mkdtemp())
        for index in range(3):
            _write(folder, f"doc{index}.md", "# T\n\n" + "Texto. " * 30)

        documents, skipped = si.folder_documents(str(folder), limit=2)

        self.assertEqual(len(documents), 2)
        self.assertEqual(len(skipped), 1)
        self.assertIn("2-document limit", skipped[0]["status"])

    def test_a_missing_folder_is_a_report(self):
        with self.assertRaises(si.UnreadableSource):
            si.folder_documents(str(Path(tempfile.mkdtemp()) / "no-existe"))


class CitationTests(unittest.TestCase):
    """The default citation is the document, not the passage."""

    def setUp(self):
        self.index = si.SourceIndex()
        self.index.add_source({"id": "a", "name": "informe.md"}, (
            "# Codec\n\nEl codec trabaja a 32 kHz y suena metalico.\n\n"
            "# Clonacion\n\nLa clonacion necesita dos segundos de habla clara."
        ))
        self.index.add_source({"id": "b", "name": "costes.md"}, (
            "# Precios\n\nLa nube cuesta quince euros al mes."
        ))

    def test_citations_are_one_row_per_document_in_use_order(self):
        results = self.index.search("codec metalico clonacion", limit=4)
        citations = si.citation_rows(results)

        names = [row["source_name"] for row in citations]
        self.assertEqual(names[0], "informe.md")
        self.assertEqual(set(names), {"informe.md"})
        # Two passages of the same document collapse into one citation.
        self.assertEqual(citations[0]["passages"], len(results))
        self.assertIn("Codec", citations[0]["headings"])

    def test_no_results_means_no_citations(self):
        self.assertEqual(si.citation_rows(self.index.search("fotosintesis")), [])


if __name__ == "__main__":
    unittest.main()
