"""PDF to markdown, plus the two forensic signals we can only get from the PDF:
document metadata and text that was never meant to be seen.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
                            {"page": pno, "kind": "white_on_white", "text": text[:200], "size": size}
                        )
                    elif size < _MICRO_FONT_PT:
                        spans.append(
                            {"page": pno, "kind": "micro_font", "text": text[:200], "size": size}
                        )

        try:
            for trace in page.get_texttrace():
                if int(trace.get("type", 0)) != _INVISIBLE_RENDER_MODE:
                    continue
                chars = trace.get("chars") or []
                text = "".join(chr(c[0]) for c in chars if isinstance(c, (list, tuple)) and c).strip()
                if text:
                    spans.append(
                        {"page": pno, "kind": "invisible_render_mode", "text": text[:200], "size": None}
                    )
        except Exception:
            # get_texttrace is best-effort; the colour and size checks above are
            # the primary signal and must not be lost to a trace failure.
            pass

    return spans


def parse_pdf(pdf_path: Path) -> ParsedPdf:
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

    return ParsedPdf(
        markdown=markdown,
        meta=meta,
        hidden_text={"found": bool(spans), "spans": spans},
        pages=pages,
        sha256=sha256_file(pdf_path),
        text_chars=len(markdown.strip()),
    )


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
