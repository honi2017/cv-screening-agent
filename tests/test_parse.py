from pathlib import Path

import pytest

from screen.parse import UnsupportedFormatError, _is_substantive, parse_pdf, sha256_file, write_parsed

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


def test_hidden_text_detected_for_invisible_render_mode(pdfs):
    p = parse_pdf(pdfs["invisible_text"])
    assert p.hidden_text["found"] is True
    kinds = {s["kind"] for s in p.hidden_text["spans"]}
    assert "invisible_render_mode" in kinds


def test_clean_cv_has_no_hidden_text(pdfs):
    p = parse_pdf(pdfs["clean"])
    assert p.hidden_text["found"] is False
    assert p.hidden_text["spans"] == []


# --- G3 false-positive guard (real-data calibration fix) --------------------
#
# Measured against 66 real CVs, gate G3 fired on 3/66 and all three were
# false positives: a large white name on a dark header banner, and two
# Google Docs -> PDF exports where the bullet marker is drawn as an
# invisible single "•" glyph. Keyword stuffing is always a run of terms, so
# _is_substantive gates `found` on span content, not merely on colour/size.


def test_is_substantive_requires_a_run_of_words():
    assert _is_substantive("forward deployed engineer SAML SFTP private markets") is True


def test_is_substantive_rejects_a_short_title():
    assert _is_substantive("Jordan Lee") is False


def test_is_substantive_rejects_a_bullet_glyph():
    assert _is_substantive("•") is False


def test_white_title_on_dark_banner_does_not_gate(pdfs):
    p = parse_pdf(pdfs["white_title"])
    assert p.hidden_text["found"] is False
    assert any(s["kind"] == "white_on_white" for s in p.hidden_text["spans"])
    assert all(s["substantive"] is False for s in p.hidden_text["spans"])


def test_bullet_glyph_run_does_not_gate(pdfs):
    p = parse_pdf(pdfs["bullet_glyphs"])
    assert p.hidden_text["found"] is False
    spans = p.hidden_text["spans"]
    assert len(spans) >= 15
    assert all(s["substantive"] is False for s in spans)


def test_keyword_stuffing_run_still_gates(pdfs):
    # Regression: the real fixtures below still trip found=True after the
    # substantive filter -- calibration must not blind the detector to
    # genuine stuffing.
    p = parse_pdf(pdfs["hidden_text"])
    assert p.hidden_text["found"] is True
    assert any(s["substantive"] is True for s in p.hidden_text["spans"])

    p2 = parse_pdf(pdfs["invisible_text"])
    assert p2.hidden_text["found"] is True
    assert any(s["substantive"] is True for s in p2.hidden_text["spans"])


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


def test_parse_docx_extracts_text_and_marks_no_hidden_text_possible(pdfs):
    p = parse_pdf(pdfs["clean_docx"])
    assert p.text_chars > 200
    assert "Northwind Data" in p.markdown
    assert "SAML" in p.markdown
    assert p.pages == 0
    assert p.meta["producer"] == ""
    assert p.meta["creator"] == ""
    assert p.hidden_text == {"found": False, "spans": []}


def test_parse_docx_sha256_matches_helper(pdfs):
    p = parse_pdf(pdfs["clean_docx"])
    assert p.sha256 == sha256_file(pdfs["clean_docx"])


def test_unsupported_extension_is_rejected(tmp_path):
    bogus = tmp_path / "resume.rtf"
    bogus.write_text("{\\rtf1 not a pdf or docx}")
    with pytest.raises(UnsupportedFormatError):
        parse_pdf(bogus)
