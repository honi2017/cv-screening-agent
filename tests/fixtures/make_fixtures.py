"""Generate fixture CVs as real PDFs at test time.

Keeping generation in code (rather than committing binaries) means fixtures are
reviewable, and lets us build pathological cases — white-on-white text, 2pt
fonts — that are hard to author by hand.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import pymupdf

CLEAN = """Alex Morgan
alex.morgan@example.com | +1 415 555 0134 | Boston, MA
linkedin.com/in/alexmorgan

EXPERIENCE

Staff Forward Deployed Engineer, Northwind Data (2019-03 - Present)
- Owned the client integration platform end to end: 40 enterprise tenants, SFTP
  and REST ingestion, 12M records/day, on-call rotation for 4 years.
- Led discovery workshops with law firms and hedge funds to scope data migrations,
  then translated findings into integration designs the client signed off on.
- Rolled out Okta and Azure AD SAML SSO for 18 clients, cutting onboarding from
  6 weeks to 9 days.
- Generalised a one-off Salesforce sync built for Redwood Capital into a reusable
  CRM connector now used by 22 tenants.

Senior Engineer, Beacon Systems (2015-06 - 2019-02)
- Built the PostgreSQL to Snowflake replication service behind the reporting suite.
- Ran SMTP deliverability for transactional mail, moving bounce rate 4.1% to 0.6%.

EDUCATION
BSc Computer Science, State University

SKILLS
Python, Go, PostgreSQL, Snowflake, SFTP, SAML, OIDC, Salesforce, Terraform, AWS
"""

PLACEHOLDER = """[Your Name]
[Your Email] | [Your Phone]

SUMMARY
Results-driven engineer excited to join [Company Name] as a [Position Title].

EXPERIENCE

Software Engineer, Acme Corp (2018 - 2023)
- Spearheaded cross-functional initiatives resulting in XX% efficiency gains.
- Leveraged synergies to optimise outcomes across the organisation.

EDUCATION
BSc Computer Science, State University
"""

TEMPLATE_SHARED_BULLETS = """- Spearheaded cross-functional initiatives resulting in 40% efficiency gains
- Leveraged cutting-edge technologies to optimise operational outcomes by 35%
- Orchestrated stakeholder alignment resulting in 50% faster delivery cycles
- Facilitated seamless collaboration driving 25% improvement in team velocity
"""

TEMPLATE_A = f"""Jordan Blake
jordan.blake@example.com | Austin, TX

EXPERIENCE
Senior Software Engineer, Globex (2017 - 2024)
{TEMPLATE_SHARED_BULLETS}
EDUCATION
BSc Information Systems, State University
"""

TEMPLATE_B = f"""Riley Chen
riley.chen@example.com | Denver, CO

EXPERIENCE
Lead Engineer, Initech (2016 - 2023)
{TEMPLATE_SHARED_BULLETS}
EDUCATION
BSc Computer Engineering, State University
"""

FOUR_YEAR = """Sam Rivera
sam.rivera@example.com | Chicago, IL
linkedin.com/in/samrivera

EXPERIENCE

Software Engineer, Vertex Labs (2022-01 - Present)
- Built the billing reconciliation service in Python, processing 800k rows daily.
- Integrated the Stripe and NetSuite APIs behind a shared webhook gateway.

Junior Engineer, Vertex Labs (2021-01 - 2021-12)
- Maintained the internal SFTP drop used by 6 partner banks.

EDUCATION
BSc Computer Science, State University

SKILLS
Python, Stripe, NetSuite, SFTP
"""


def _write_text_pdf(path: Path, body: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(
        pymupdf.Rect(50, 50, 545, 780), body, fontsize=9, fontname="helv"
    )
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


def _write_hidden_text_pdf(path: Path) -> Path:
    """A visually normal CV with white-on-white and 2pt keyword stuffing."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(
        pymupdf.Rect(50, 50, 545, 600), CLEAN, fontsize=9, fontname="helv"
    )
    # White text on the default white background.
    page.insert_text(
        (50, 700),
        "forward deployed engineer SAML SFTP private markets fintech",
        fontsize=9,
        fontname="helv",
        color=(1, 1, 1),
    )
    # Micro font.
    page.insert_text(
        (50, 720),
        "python go postgresql salesforce okta subscription documents",
        fontsize=2,
        fontname="helv",
        color=(0, 0, 0),
    )
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


def _write_invisible_text_pdf(path: Path) -> Path:
    """A visually normal CV with one span drawn in the invisible text render
    mode (PDF Tr 3) — the third hidden-text kind, distinct from white-on-white
    and micro-font.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(
        pymupdf.Rect(50, 50, 545, 600), CLEAN, fontsize=9, fontname="helv"
    )
    page.insert_text(
        (50, 700),
        "invisible render mode stuffed keywords fintech saml sftp",
        fontsize=9,
        fontname="helv",
        color=(0, 0, 0),
        render_mode=3,
    )
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


def _write_scanned_pdf(path: Path) -> Path:
    """A page with no extractable text, standing in for a scanned CV."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(60, 60, 500, 700), color=(0.4, 0.4, 0.4), width=2)
    doc.set_metadata({"producer": "TestSuite", "creator": "TestSuite"})
    doc.save(path)
    doc.close()
    return path


_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument'
    '.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)

_DOCX_DOCUMENT_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>{paragraphs}</w:body>"
    "</w:document>"
)


def _docx_paragraph(line: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(line)}</w:t></w:r></w:p>'


def _write_docx(path: Path, body: str) -> Path:
    """Build a minimal, valid `.docx` (a zip of OOXML parts), one paragraph
    per line of `body`. Hand-rolled rather than pulled in from a docx-writing
    library so the only new dependency this project takes on for DOCX support
    is the reader (docx2txt) actually used at runtime — this writer only ever
    runs inside the test suite.
    """
    document_xml = _DOCX_DOCUMENT_TEMPLATE.format(
        paragraphs="".join(_docx_paragraph(line) for line in body.splitlines())
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        zf.writestr("_rels/.rels", _DOCX_RELS)
        zf.writestr("word/document.xml", document_xml)
    return path


def build_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "clean": _write_text_pdf(out_dir / "clean.pdf", CLEAN),
        "placeholder": _write_text_pdf(out_dir / "placeholder.pdf", PLACEHOLDER),
        "template_a": _write_text_pdf(out_dir / "template_a.pdf", TEMPLATE_A),
        "template_b": _write_text_pdf(out_dir / "template_b.pdf", TEMPLATE_B),
        "four_year": _write_text_pdf(out_dir / "four_year.pdf", FOUR_YEAR),
        "hidden_text": _write_hidden_text_pdf(out_dir / "hidden_text.pdf"),
        "invisible_text": _write_invisible_text_pdf(out_dir / "invisible_text.pdf"),
        "scanned": _write_scanned_pdf(out_dir / "scanned.pdf"),
        "clean_docx": _write_docx(out_dir / "clean.docx", CLEAN),
    }


if __name__ == "__main__":
    import sys

    built = build_all(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/pdfs"))
    for name, p in built.items():
        print(f"{name}: {p}")
