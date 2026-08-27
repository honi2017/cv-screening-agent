"""PDF and DOCX resumes to markdown/text, plus the two forensic signals we can
only get from a PDF: document metadata and text that was never meant to be
seen.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docx2txt
import pymupdf
import pymupdf4llm

# sRGB integer for pure white, as returned by page.get_text("dict").
_WHITE = 0xFFFFFF
_LUMINANCE_HIDDEN = 0.92
_MICRO_FONT_PT = 4.0
_INVISIBLE_RENDER_MODE = 3


@dataclass(frozen=True)
class ParsedPdf:
    markdown: str
    meta: dict[str, Any]
    hidden_text: dict[str, Any]
    pages: int
    sha256: str
    text_chars: int

    def to_meta_json(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "pages": self.pages,
            "text_chars": self.text_chars,
            "pdf_meta": self.meta,
            "hidden_text": self.hidden_text,
        }


class UnsupportedFormatError(ValueError):
    """Raised when a resume's extension is neither `.pdf` nor `.docx`.

    Callers (the precheck stage) should catch this and record the candidate
    as `needs_review: unsupported_format` rather than letting it propagate.
    """

    reason = "unsupported_format"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def _suppressed_pymupdf_messages():
    """Silence pymupdf4llm's internal OCR-engine probe (e.g. "Using
    Tesseract for OCR processing."), which writes through pymupdf.message()
    regardless of show_progress. That stream is bound directly to sys.stdout
    at import time — not looked up dynamically — so contextlib.redirect_stdout
    has no effect on it; only PyMuPDF's own pymupdf.set_messages() API can
    redirect it. This is noise, not an error signal: PyMuPDF reports real
    failures by raising, not printing, so redirecting here loses no error
    information. Restores the previous destination on exit, so this only
    affects the wrapped call. Do not remove.
    """
    previous = pymupdf._g_out_message
    pymupdf.set_messages(stream=io.StringIO())
    try:
        yield
    finally:
        pymupdf.set_messages(stream=previous)


def _is_substantive(text: str) -> bool:
    """True when a hidden span carries enough content to be keyword stuffing.

    G3 is a hard elimination, so it must not fire on rendering artifacts.
    Measured against 66 real CVs, the only white spans present were a large
    white name on a dark header banner and runs of 0.75pt "•" bullet glyphs from
    a Google Docs PDF export — 3 of 66 candidates would have been eliminated for
    how their PDF was produced. Genuine stuffing is always a run of terms.
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*", text or "")
    alnum = sum(ch.isalnum() for ch in text or "")
    return len(words) >= 5 and alnum >= 25


def _luminance(color_int: int) -> float:
    r = ((color_int >> 16) & 0xFF) / 255
    g = ((color_int >> 8) & 0xFF) / 255
    b = (color_int & 0xFF) / 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hidden_spans(doc: pymupdf.Document) -> list[dict[str, Any]]:
    """Find text a human reader would never see.

    Three kinds: white (or near-white) on the default white page, fonts below
    4pt, and spans drawn with the invisible text render mode.
    """
    spans: list[dict[str, Any]] = []

    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    color = int(span.get("color", 0))
                    size = float(span.get("size", 12.0))
                    if color == _WHITE or _luminance(color) >= _LUMINANCE_HIDDEN:
                        spans.append(
                            {
                                "page": pno,
                                "kind": "white_on_white",
                                "text": text[:200],
                                "size": size,
                                "substantive": _is_substantive(text),
                            }
                        )
                    elif size < _MICRO_FONT_PT:
                        spans.append(
                            {
                                "page": pno,
                                "kind": "micro_font",
                                "text": text[:200],
                                "size": size,
                                "substantive": _is_substantive(text),
                            }
                        )

        try:
            for trace in page.get_texttrace():
                if int(trace.get("type", 0)) != _INVISIBLE_RENDER_MODE:
                    continue
                chars = trace.get("chars") or []
                text = "".join(chr(c[0]) for c in chars if isinstance(c, (list, tuple)) and c).strip()
                if text:
                    spans.append(
                        {
                            "page": pno,
                            "kind": "invisible_render_mode",
                            "text": text[:200],
                            "size": None,
                            "substantive": _is_substantive(text),
                        }
                    )
        except Exception:
            # get_texttrace is best-effort; the colour and size checks above are
            # the primary signal and must not be lost to a trace failure.
            pass

    return spans


def _parse_pdf_file(pdf_path: Path) -> ParsedPdf:
    doc = pymupdf.open(pdf_path)
    try:
        with _suppressed_pymupdf_messages():
            markdown = pymupdf4llm.to_markdown(doc, show_progress=False)
        raw_meta = doc.metadata or {}
        meta = {
            "producer": (raw_meta.get("producer") or "").strip(),
            "creator": (raw_meta.get("creator") or "").strip(),
            "created": (raw_meta.get("creationDate") or "").strip(),
            "modified": (raw_meta.get("modDate") or "").strip(),
        }
        spans = _hidden_spans(doc)
        pages = doc.page_count
    finally:
        doc.close()

    found = any(s["substantive"] for s in spans)

    return ParsedPdf(
        markdown=markdown,
        meta=meta,
        hidden_text={"found": found, "spans": spans},
        pages=pages,
        sha256=sha256_file(pdf_path),
        text_chars=len(markdown.strip()),
    )


def _parse_docx_file(docx_path: Path) -> ParsedPdf:
    """Extract text from a `.docx` resume.

    DOCX has no equivalent of the PDF white-on-white or invisible-render-mode
    keyword-stuffing tricks `_hidden_spans` looks for above — a plain text
    extractor never sees a font colour or a render mode, those are PDF paint
    operators — so `hidden_text` is always empty here and gate G3 (hidden
    text) simply cannot fire on a DOCX resume. `pages` has no cheap
    equivalent without a layout/renderer, so it is reported as 0 rather than
    guessed, and `producer`/`creator` are left blank since docx2txt does not
    surface core-properties metadata.
    """
    text = docx2txt.process(str(docx_path)) or ""
    return ParsedPdf(
        markdown=text,
        meta={"producer": "", "creator": "", "created": "", "modified": ""},
        hidden_text={"found": False, "spans": []},
        pages=0,
        sha256=sha256_file(docx_path),
        text_chars=len(text.strip()),
    )


def parse_pdf(path: Path) -> ParsedPdf:
    """Parse a resume file into a `ParsedPdf`, dispatching on file extension.

    Kept as `parse_pdf` (rather than a more generic name) for interface
    stability — downstream code depends on this exact name — even though it
    now also handles `.docx`.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf_file(path)
    if suffix == ".docx":
        return _parse_docx_file(path)
    raise UnsupportedFormatError(f"unsupported resume format: {path.suffix or '(none)'}")


def write_parsed(parsed: ParsedPdf, md_path: Path, meta_path: Path) -> None:
    """Write atomically: temp file then rename, so an interrupted run leaves no
    half-written artifact for the next run to trust.
    """
    for path, payload in (
        (md_path, parsed.markdown),
        (meta_path, json.dumps(parsed.to_meta_json(), indent=2)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(path)
