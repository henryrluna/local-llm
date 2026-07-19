from pathlib import Path

from pypdf import PdfReader

from research_harness.reporting import main_report_word_count, render_pdf, validate_citations


def test_citation_validation_rejects_unknown_ids():
    paragraph = "This is a substantive factual paragraph with enough words to trigger the citation coverage checker and ensure that evidence linkage is tested correctly [9]."
    errors = validate_citations(paragraph, {"1"})
    assert any("9" in error for error in errors)


def test_legacy_source_prefix_is_still_accepted_during_transition():
    paragraph = "This substantive paragraph retains compatibility with an older saved report while the newly generated reports use plain numeric citations [S1]."
    assert validate_citations(paragraph, {"1"}) == []


def test_main_report_word_count_excludes_front_and_back_matter():
    markdown = "# Executive Summary\n\nfront matter words\n\n# Full Research Report\n\n## Finding\n\n" + ("core evidence [1] " * 100) + "\n\n# Contradictions, Uncertainty, and Limitations\n\nback matter"
    assert main_report_word_count(markdown) == 200


def test_rendered_pdf_has_headers_footers_and_pages(tmp_path: Path):
    summary = "\n\n".join(("Concise executive evidence remains focused and cited [1]. " * 10).strip() for _ in range(4))
    body = "\n\n".join(
        f"## Finding {i}\n\n" + ("Evidence-backed analysis explains the finding in readable prose [1]. " * 55)
        for i in range(1, 9)
    )
    markdown = f"# Executive Summary\n\n{summary}\n\n# Research Question and Scope\n\nA bounded test scope.\n\n# Full Research Report\n\n{body}\n\n# Contradictions, Uncertainty, and Limitations\n\nKnown limits.\n\n# Bibliography\n\n[1] Example. https://example.com.\n\n# Collection Notes\n\nNo errors.\n\n# Independent Evidence and Citation Audit\n\nCoverage passes [1]."
    path = tmp_path / "report.pdf"
    pages = render_pdf(markdown, path, "Test Research Report")
    assert pages >= 10
    reader = PdfReader(str(path))
    page_text = [page.extract_text() or "" for page in reader.pages]
    executive_pages = [index for index, text in enumerate(page_text) if "Executive Summary" in text]
    assert executive_pages == [1]
    assert "Research Question and Scope" in page_text[2]


def test_pdf_normalizes_unicode_hyphens_spaces_and_quotes(tmp_path: Path):
    markdown = """# Full Research Report

Theil's post describes interlinked supply\u2011chain units and a public\u2011facing agenda. The article\u202f[1] says, \u201cI hope you understand my why.\u201d

# Contradictions, Uncertainty, and Limitations

None.
"""
    path = tmp_path / "unicode.pdf"
    render_pdf(markdown, path, "Unicode punctuation regression")
    text = "".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    normalized = " ".join(text.split())
    assert "supply-chain" in normalized
    assert "public-facing" in normalized
    assert "article [1]" in normalized
    assert "“I hope you understand my why.”" in normalized


def test_pdf_preserves_unicode_and_renders_markdown_italics(tmp_path: Path):
    markdown = """# Full Research Report

The café described *measured uncertainty* and _independent verification_.

- A Unicode bullet with naïve assumptions.

# Contradictions, Uncertainty, and Limitations

None.
"""
    path = tmp_path / "unicode-italics.pdf"
    render_pdf(markdown, path, "Unicode and italics regression")
    reader = PdfReader(str(path))
    text = " ".join((page.extract_text() or "") for page in reader.pages)
    assert "café" in text
    assert "naïve" in text
    assert "*measured uncertainty*" not in text
    assert "_independent verification_" not in text
    fonts = {
        font_name
        for page in reader.pages
        for font_name in (page.get("/Resources", {}).get("/Font", {}) or {}).keys()
    }
    assert len(fonts) >= 2
