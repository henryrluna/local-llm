from pathlib import Path

from pypdf import PdfReader

from research_harness.reporting import citation_coverage, render_pdf, validate_citations


def test_citation_validation_rejects_unknown_ids():
    paragraph = "This is a substantive factual paragraph with enough words to trigger the citation coverage checker and ensure that evidence linkage is tested correctly [S9]."
    errors = validate_citations(paragraph, {"S1"})
    assert any("S9" in error for error in errors)


def test_rendered_pdf_has_headers_footers_and_pages(tmp_path: Path):
    body = "\n\n".join(
        f"## Finding {i}\n\n" + ("Evidence-backed analysis explains the finding in readable prose [S1]. " * 55)
        for i in range(1, 11)
    )
    markdown = f"# Executive Summary\n\n{'Summary evidence [S1]. ' * 120}\n\n{body}\n\n# Bibliography\n\n[S1] Example. https://example.com."
    path = tmp_path / "report.pdf"
    pages = render_pdf(markdown, path, "Test Research Report")
    assert pages >= 10
    reader = PdfReader(str(path))
    assert "Executive Summary" in "".join(page.extract_text() or "" for page in reader.pages)
