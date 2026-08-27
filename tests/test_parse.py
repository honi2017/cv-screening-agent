from pathlib import Path

import pytest

from screen.parse import parse_pdf, sha256_file, write_parsed

import sys

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import build_all  # noqa: E402


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return build_all(tmp_path_factory.mktemp("pdfs"))


def test_parse_clean_extracts_text_and_page_count(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.pages == 1
    assert p.text_chars > 200
    assert "Northwind Data" in p.markdown
    assert "SAML SSO" in p.markdown or "SAML" in p.markdown


def test_sha256_is_stable_and_matches_helper(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.sha256 == sha256_file(pdfs["clean"])
    assert len(p.sha256) == 64


def test_metadata_captures_producer_and_creation(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.meta["producer"] == "TestSuite"
    assert "created" in p.meta


def test_hidden_text_detected_for_white_and_micro_font(pdfs):
    p = parse_pdf(pdfs["hidden_text"])
    assert p.hidden_text["found"] is True
    kinds = {s["kind"] for s in p.hidden_text["spans"]}
    assert "white_on_white" in kinds
    assert "micro_font" in kinds


def test_clean_cv_has_no_hidden_text(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.hidden_text["found"] is False
    assert p.hidden_text["spans"] == []


def test_scanned_pdf_yields_almost_no_text(pdfs):
    p = parse_pdf(pdfs["scanned"])
    assert p.text_chars < 200


def test_write_parsed_writes_markdown_and_meta(tmp_path, pdfs):
    p = parse_pdf(pdfs["clean"])
    md, meta = tmp_path / "1.md", tmp_path / "1.meta.json"
    write_parsed(p, md, meta)
    assert md.read_text() == p.markdown
    import json

    loaded = json.loads(meta.read_text())
    assert loaded["sha256"] == p.sha256
    assert loaded["pages"] == 1
    assert loaded["hidden_text"]["found"] is False
