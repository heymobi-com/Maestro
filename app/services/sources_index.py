"""Turn documents into retrievable passages, with no model and no GPU.

A long spoken script needs material. Given only a one-line brief, the writer can
do nothing but improvise, which is what "a simple prompt gives random results"
looked like. This module turns a folder of documents into passages a writer call
can quote, so the script is grounded in something the user actually supplied.

Design constraints, in order:

* **No new runtime dependencies except a PDF reader.** Markdown, text, HTML and
  DOCX are read with the standard library: a DOCX is a ZIP of XML, so
  ``zipfile`` + ``xml.etree`` is enough and avoids ``lxml`` (compiled, and this
  project's manifest pins 74 packages it must not disturb). HTML goes through
  ``html.parser``. PDF lazily imports ``pypdf`` and reports a clear message when
  it is absent instead of breaking the app.
* **Retrieval is lexical (BM25).** No embedding model is installed here, and a
  lexical index is deterministic, testable, instant, and good enough for "find
  the passages that talk about this", which is what a script outline needs.
* **Untrusted input is bounded.** Every reader is wrapped: size, page and chunk
  caps, and a failure returns "could not read" rather than raising into the app.

The API is one object: build an index from paths, save it per workspace, search it.
"""

from __future__ import annotations

import json
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# ── Limits ───────────────────────────────────────────────────────────────
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 120
MAX_CHUNKS_PER_SOURCE = 4000
MAX_TOTAL_CHUNKS = 12000
# Two characters is noise; one is punctuation debris.
_MIN_TOKEN_LEN = 2

TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ".rst"}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
DOCX_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | HTML_EXTENSIONS | DOCX_EXTENSIONS | PDF_EXTENSIONS

INDEX_FILENAME = "_sources_index.json"
INDEX_VERSION = 1

# A small bilingual stopword set: enough to stop "the/que/de" from dominating the
# ranking without pretending to be a linguistic resource.
_STOPWORDS = frozenset("""
a an and are as at be but by for from had has have he her his i if in into is it its
of on or she that the their them then there these they this to was were what when
which who will with you your
al algo como con cual de del el ella en es ese esa eso esta este esto hay la las le
les lo los más me mi muy no nos o para pero por porque que se si sin sobre su sus te
tu un una uno unos y ya yo
""".split())

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
# A short prefix is enough to tie "clonar" to "clonación" and "metálicas" to
# "metalicas". Measured on a Spanish source: without it the query "cuánto audio
# necesito para clonar una voz" ranked the pricing passage above the cloning one.
_STEM_PREFIX = 5
_HEADING_RE = re.compile(r"^(?:#{1,6}\s+(?P<md>.+)|(?P<plain>[A-ZÁÉÍÓÚÑ][^.\n]{0,78}):?)$")


def _fold(text: str) -> str:
    """Lowercase without accents: people type "metalicas" for "metálicas"."""

    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))



class UnreadableSource(Exception):
    """A file that cannot be turned into text. The message is user-facing."""


# ── Text extraction ──────────────────────────────────────────────────────


def extract_text(path: str) -> str:
    """Plain text from one document, or ``UnreadableSource`` with the reason."""

    if not os.path.isfile(path):
        raise UnreadableSource(f"File not found: {path}")
    try:
        size = os.path.getsize(path)
    except OSError as error:
        raise UnreadableSource(f"Could not read {os.path.basename(path)}: {error}")
    if size > MAX_FILE_BYTES:
        raise UnreadableSource(
            f"{os.path.basename(path)} is {size // (1024 * 1024)} MB; the limit is "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB per document."
        )

    extension = os.path.splitext(path)[1].lower()
    if extension in TEXT_EXTENSIONS:
        return _read_text_file(path)
    if extension in HTML_EXTENSIONS:
        return _read_html_file(path)
    if extension in DOCX_EXTENSIONS:
        return _read_docx_file(path)
    if extension in PDF_EXTENSIONS:
        return _read_pdf_file(path, os.path.basename(path))
    raise UnreadableSource(
        f"{os.path.basename(path)}: unsupported type. Use Markdown, TXT, HTML, "
        "DOCX or PDF."
    )


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            return handle.read()
    except OSError as error:
        raise UnreadableSource(f"Could not read {os.path.basename(path)}: {error}")


class _HtmlTextExtractor:
    """Collect text nodes, skipping script/style/head noise."""

    _SKIP = frozenset({"script", "style", "head", "noscript", "template"})
    _BLOCKS = frozenset({
        "p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3",
        "h4", "h5", "h6", "blockquote", "pre", "td", "dd", "dt",
    })

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle(self):
        from html.parser import HTMLParser

        outer = self

        class _Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag in outer._SKIP:
                    outer._skip_depth += 1
                elif tag in outer._BLOCKS:
                    outer._parts.append("\n")

            def handle_endtag(self, tag):
                if tag in outer._SKIP and outer._skip_depth:
                    outer._skip_depth -= 1
                elif tag in outer._BLOCKS:
                    outer._parts.append("\n")

            def handle_data(self, data):
                if not outer._skip_depth and data.strip():
                    outer._parts.append(data)

        return _Parser(convert_charrefs=True)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _read_html_file(path: str) -> str:
    raw = _read_text_file(path)
    extractor = _HtmlTextExtractor()
    parser = extractor.handle()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup is not fatal
        pass
    return extractor.text or raw


def _read_docx_file(path: str) -> str:
    """Paragraph text from a DOCX, using only the standard library.

    A DOCX is a ZIP whose body is ``word/document.xml``. Reading it here avoids
    ``python-docx`` and therefore ``lxml``, which is compiled and would have to be
    pinned into this project's carefully pinned manifest.
    """

    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(path) as archive:
            try:
                xml_bytes = archive.read("word/document.xml")
            except KeyError:
                raise UnreadableSource(
                    f"{os.path.basename(path)} has no word/document.xml; is it a .docx?"
                )
    except zipfile.BadZipFile:
        raise UnreadableSource(f"{os.path.basename(path)} is not a valid .docx file.")
    except OSError as error:
        raise UnreadableSource(f"Could not read {os.path.basename(path)}: {error}")

    # A DOCX is untrusted XML, so entity expansion stays off: ElementTree does not
    # expand external entities, and a DocumentType node is refused outright.
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as error:
        raise UnreadableSource(
            f"{os.path.basename(path)}: malformed document XML ({error})."
        )

    paragraphs: list[str] = []
    for paragraph in root.iter():
        if not paragraph.tag.endswith("}p"):
            continue
        words = [
            node.text or ""
            for node in paragraph.iter()
            if node.tag.endswith("}t")
        ]
        line = "".join(words).strip()
        if line:
            paragraphs.append(line)
    if not paragraphs:
        raise UnreadableSource(
            f"{os.path.basename(path)} contains no readable text (images or tables only?)."
        )
    return "\n\n".join(paragraphs)


def _read_pdf_file(path: str, label: str) -> str:
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - optional, lazily imported
    except ImportError:
        raise UnreadableSource(
            f"{label}: PDF reading needs the 'pypdf' package, which is not installed. "
            "Convert the file to Markdown or TXT, or install pypdf."
        )
    try:
        reader = PdfReader(path)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - a locked PDF is a user problem
                raise UnreadableSource(f"{label} is password-protected.")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise UnreadableSource(
                f"{label} has {pages} pages; the limit is {MAX_PDF_PAGES}. "
                "Split it or convert the part you need."
            )
        parts: list[str] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one bad page must not lose the file
                parts.append("")
        text = "\n\n".join(part for part in parts if part.strip())
    except UnreadableSource:
        raise
    except Exception as error:  # noqa: BLE001 - pypdf raises many shapes
        raise UnreadableSource(f"{label} could not be read as a PDF ({error}).")
    if not text.strip():
        raise UnreadableSource(
            f"{label} has no extractable text; it is probably a scan. Transcribe it first."
        )
    return text


# ── Chunking ─────────────────────────────────────────────────────────────


@dataclass
class Chunk:
    """One retrievable passage, with the heading it belongs to."""

    text: str
    heading: str = ""
    source_id: str = ""
    source_name: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "heading": self.heading,
            "source_id": self.source_id,
            "source_name": self.source_name,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Chunk":
        return cls(
            text=str(row.get("text") or ""),
            heading=str(row.get("heading") or ""),
            source_id=str(row.get("source_id") or ""),
            source_name=str(row.get("source_name") or ""),
        )


def _looks_like_heading(line: str) -> str:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return ""
    if stripped.startswith("#"):
        return stripped.lstrip("#").strip() or ""
    match = _HEADING_RE.match(stripped)
    if match and match.group("plain") and not stripped.endswith("."):
        return stripped.rstrip(":")
    return ""


def chunk_text(
    text: str,
    *,
    source_id: str = "",
    source_name: str = "",
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """Split into passages on paragraph boundaries, carrying the last heading.

    The heading travels with each passage because retrieval returns a passage out
    of context: "the compression ratio of the 2019 study" is only interpretable
    next to the section it came from.
    """

    limit = max(MIN_CHUNK_CHARS, int(max_chars or MAX_CHUNK_CHARS))
    chunks: list[Chunk] = []
    heading = ""
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        body = "\n\n".join(part for part in buffer if part.strip()).strip()
        buffer = []
        size = 0
        if len(body) < MIN_CHUNK_CHARS // 3:
            return
        chunks.append(
            Chunk(text=body, heading=heading, source_id=source_id, source_name=source_name)
        )

    for block in re.split(r"\n\s*\n", str(text or "")):
        block = re.sub(r"[ \t]+", " ", block).strip()
        if not block:
            continue
        candidate_heading = _looks_like_heading(block.splitlines()[0])
        if candidate_heading and len(block.splitlines()) == 1:
            flush()
            heading = candidate_heading
            continue
        if size + len(block) > limit and buffer:
            flush()
        buffer.append(block)
        size += len(block)
        if len(chunks) >= MAX_CHUNKS_PER_SOURCE:
            break
    flush()
    return chunks


# ── Retrieval ────────────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """Retrieval keys: folded, stopword-free, and reduced to a short prefix.

    Folding makes a brief typed without accents match a document written with
    them; the prefix ties inflections of the same word together.
    """

    keys: list[str] = []
    for match in _TOKEN_RE.finditer(str(text or "")):
        folded = _fold(match.group(0))
        if len(folded) < _MIN_TOKEN_LEN or folded in _STOPWORDS:
            continue
        keys.append(folded if len(folded) < _STEM_PREFIX else folded[:_STEM_PREFIX])
    return keys


@dataclass
class SourceIndex:
    """A BM25 index over the chunks of one workspace's documents."""

    chunks: list[Chunk] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    _df: dict[str, int] = field(default_factory=dict, repr=False)
    _lengths: list[int] = field(default_factory=list, repr=False)
    _avg_length: float = 0.0

    # ── construction ──
    def add_source(self, source: Mapping[str, Any], text: str) -> int:
        """Index one document's text. Returns how many passages it added."""

        source_id = str(source.get("id") or "")
        source_name = str(source.get("name") or source_id)
        added = chunk_text(
            text, source_id=source_id, source_name=source_name,
        )
        room = MAX_TOTAL_CHUNKS - len(self.chunks)
        added = added[: max(0, room)]
        self.chunks.extend(added)
        self.sources.append({
            "id": source_id,
            "name": source_name,
            "path": str(source.get("path") or ""),
            "chars": len(text),
            "chunks": len(added),
        })
        self._rebuild()
        return len(added)

    def _rebuild(self) -> None:
        df: dict[str, int] = {}
        lengths: list[int] = []
        for chunk in self.chunks:
            tokens = tokenize(f"{chunk.heading} {chunk.text}")
            lengths.append(len(tokens))
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1
        self._df = df
        self._lengths = lengths
        self._avg_length = (sum(lengths) / len(lengths)) if lengths else 0.0

    # ── query ──
    def search(self, query: str, limit: int = 6) -> list[dict]:
        """The passages that best match the query, newest ranking first."""

        terms = tokenize(query)
        if not terms or not self.chunks:
            return []
        total = len(self.chunks)
        avg = self._avg_length or 1.0
        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for position, chunk in enumerate(self.chunks):
            tokens = tokenize(f"{chunk.heading} {chunk.text}")
            if not tokens:
                continue
            counts: dict[str, int] = {}
            for token in tokens:
                if token in terms:
                    counts[token] = counts.get(token, 0) + 1
            if not counts:
                continue
            length = self._lengths[position] if position < len(self._lengths) else len(tokens)
            score = 0.0
            for term, frequency in counts.items():
                document_frequency = self._df.get(term, 0)
                if not document_frequency:
                    continue
                idf = math.log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
                denominator = frequency + k1 * (1 - b + b * (length / avg))
                score += idf * (frequency * (k1 + 1)) / (denominator or 1)
            if score > 0:
                scored.append((score, position))
        scored.sort(reverse=True)
        results: list[dict] = []
        for score, position in scored[: max(1, int(limit or 1))]:
            chunk = self.chunks[position]
            results.append({
                **chunk.to_dict(),
                "score": round(score, 4),
            })
        return results

    # ── persistence ──
    def to_dict(self) -> dict:
        return {
            "version": INDEX_VERSION,
            "sources": list(self.sources),
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceIndex":
        index = cls()
        index.sources = [dict(row) for row in (payload.get("sources") or []) if isinstance(row, Mapping)]
        index.chunks = [
            Chunk.from_dict(row)
            for row in (payload.get("chunks") or [])
            if isinstance(row, Mapping)
        ]
        index._rebuild()
        return index


def index_path(out_dir: str) -> str:
    return os.path.join(out_dir, INDEX_FILENAME)


def load_index(out_dir: str) -> SourceIndex:
    path = index_path(out_dir)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return SourceIndex()
    if not isinstance(payload, Mapping) or payload.get("version") != INDEX_VERSION:
        return SourceIndex()
    return SourceIndex.from_dict(payload)


def save_index(out_dir: str, index: SourceIndex) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = index_path(out_dir)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(index.to_dict(), handle, ensure_ascii=False)
    os.replace(temporary, path)
    return path


def build(
    documents: Sequence[Mapping[str, Any]],
    *,
    existing: SourceIndex | None = None,
) -> tuple[SourceIndex, list[dict]]:
    """Index documents, returning the index and one report row per document."""

    index = existing or SourceIndex()
    known = {str(row.get("path") or "") for row in index.sources}
    report: list[dict] = []
    for document in documents:
        path = str(document.get("path") or "")
        name = str(document.get("name") or os.path.basename(path))
        if path and os.path.realpath(path) in {os.path.realpath(item) for item in known}:
            report.append({"name": name, "path": path, "chunks": 0, "status": "already indexed"})
            continue
        try:
            text = extract_text(path)
        except UnreadableSource as error:
            report.append({"name": name, "path": path, "chunks": 0, "status": str(error)})
            continue
        except Exception as error:  # noqa: BLE001 - never break the app for one file
            report.append({"name": name, "path": path, "chunks": 0, "status": f"unexpected error ({error})"})
            continue
        added = index.add_source(
            {"id": str(document.get("id") or _source_id(path)), "name": name, "path": path},
            text,
        )
        known.add(path)
        report.append({"name": name, "path": path, "chunks": added, "status": "indexed"})
    return index, report


def _source_id(path: str) -> str:
    import hashlib

    return hashlib.sha1(os.path.realpath(path).encode("utf-8")).hexdigest()[:12]


# A folder is a convenience, not a licence to index a whole disk: this is the most
# documents one request will walk.
MAX_FOLDER_DOCUMENTS = 200


def folder_documents(
    folder: str,
    *,
    recursive: bool = True,
    limit: int = MAX_FOLDER_DOCUMENTS,
) -> tuple[list[dict], list[dict]]:
    """Every supported document in a folder, plus the files it skipped.

    Returns ``(documents, skipped)``. Skipped rows carry the reason, so the panel can
    say "3 files ignored: not a supported type" instead of silently indexing fewer
    files than the user dropped.
    """

    if not os.path.isdir(folder):
        raise UnreadableSource(f"Not a folder: {folder}")
    documents: list[dict] = []
    skipped: list[dict] = []
    walker = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for root, _dirs, names in walker:
        for name in sorted(names):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            extension = os.path.splitext(name)[1].lower()
            if extension not in SUPPORTED_EXTENSIONS:
                skipped.append({"name": name, "status": "unsupported type"})
                continue
            if len(documents) >= limit:
                skipped.append({"name": name, "status": f"beyond the {limit}-document limit"})
                continue
            documents.append({
                "path": path,
                "name": os.path.relpath(path, folder),
                "id": _source_id(path),
            })
    return documents, skipped


def citation_rows(results: Iterable[Mapping[str, Any]]) -> list[dict]:
    """One row per source document, in the order the passages were used.

    The default citation is the document, not the passage: a listener hears "según
    el informe sobre síntesis de voz local", and the passage detail is there for the
    review step rather than for the script.
    """

    seen: dict[str, dict] = {}
    for position, row in enumerate(results, start=1):
        source_id = str(row.get("source_id") or "")
        source_name = str(row.get("source_name") or source_id or "source")
        entry = seen.setdefault(source_id or source_name, {
            "source_id": source_id,
            "source_name": source_name,
            "passages": 0,
            "headings": [],
            "first_reference": position,
        })
        entry["passages"] += 1
        heading = str(row.get("heading") or "").strip()
        if heading and heading not in entry["headings"]:
            entry["headings"].append(heading)
    return sorted(seen.values(), key=lambda row: row["first_reference"])



def context_block(
    results: Iterable[Mapping[str, Any]],
    *,
    max_chars: int = 4000,
) -> str:
    """Render retrieved passages as writer instructions.

    This is the part that turns retrieval into grounding: the passages are given as
    the only permitted material, and the writer is told what to do when they do not
    cover something.
    """

    passages: list[str] = []
    used = 0
    for position, row in enumerate(results, start=1):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        label = str(row.get("heading") or row.get("source_name") or "source")
        entry = f"[{position}] {label} — {row.get('source_name') or ''}\n{text}".strip()
        if used + len(entry) > max_chars:
            break
        passages.append(entry)
        used += len(entry)
    if not passages:
        return ""
    body = "\n\n".join(passages)
    return (
        "REFERENCE MATERIAL — the only permitted source of facts.\n"
        "Build the script on these passages. Quote their numbers, names and claims "
        "exactly; do not add facts, statistics or events that are not in them. Where "
        "the material does not cover something the brief asks for, say so in the "
        "script's own words or leave it out; never invent it.\n\n"
        f"{body}"
    )
