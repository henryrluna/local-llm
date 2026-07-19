from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


def citation_ids(text: str) -> set[str]:
    return {identifier.removeprefix("S") for identifier in re.findall(r"\[((?:S)?\d+)\]", text)}


def main_report_word_count(markdown: str) -> int:
    match = re.search(
        r"^# Full Research Report\s*$\n(.*?)(?=^# Contradictions, Uncertainty, and Limitations\s*$)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return 0
    body = re.sub(r"^#{1,6}\s+.*$", "", match.group(1), flags=re.MULTILINE)
    body = re.sub(r"\[(?:S)?\d+\]", "", body)
    return len(re.findall(r"\b[\w'-]+\b", body))


def citation_coverage(markdown: str) -> tuple[float, list[str]]:
    # Bibliography and evidence excerpts are themselves the citation targets, so
    # they are excluded from the prose-coverage denominator.
    main_report = re.split(r"^# (?:Bibliography|Appendix:)", markdown, maxsplit=1, flags=re.MULTILINE)[0]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", main_report) if p.strip()]
    substantive = [
        p for p in paragraphs
        if len(p.split()) >= 20 and not p.startswith(("#", "-", "*", "|")) and "Bibliography" not in p
    ]
    missing = [p for p in substantive if not citation_ids(p)]
    if not substantive:
        return 0.0, []
    return (len(substantive) - len(missing)) / len(substantive), missing


def validate_citations(markdown: str, valid_ids: set[str]) -> list[str]:
    errors: list[str] = []
    used = citation_ids(markdown)
    normalized_valid = {str(identifier).removeprefix("S") for identifier in valid_ids}
    unknown = sorted(used - normalized_valid, key=lambda value: int(value))
    if unknown:
        errors.append(f"Unknown citation IDs: {', '.join(unknown)}")
    coverage, missing = citation_coverage(markdown)
    if coverage < 0.70:
        errors.append(f"Citation coverage is {coverage:.0%}; at least 70% is required ({len(missing)} uncited paragraphs)")
    if not used:
        errors.append("No evidence citations were found")
    return errors


def _inline_markup(text: str) -> str:
    # ReportLab's bundled Helvetica font is WinAnsi-based. Replace unsupported
    # punctuation with readable ASCII equivalents before encoding. In
    # particular, U+2011 used to render as a question mark inside words.
    punctuation = str.maketrans({
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
        "\u2014": "-", "\u2015": "-", "\u2212": "-",
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
        "\u2026": "...",
    })
    text = text.translate(punctuation)
    text = "".join(" " if unicodedata.category(char) == "Zs" else char for char in text)
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    links: list[str] = []

    def stash_link(match: re.Match[str]) -> str:
        candidate = match.group(0)
        url = candidate.rstrip(".,;:!?)]}")
        trailing = candidate[len(url):]
        index = len(links)
        safe_url = escape(url, {"'": "&apos;", '"': "&quot;"})
        links.append(f"<link href='{safe_url}' color='#3467eb'>{safe_url}</link>")
        return f"URLPLACEHOLDER{index}END{trailing}"

    text = re.sub(r"https?://[^\s<>'\"]+", stash_link, text)
    value = escape(text)
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"`(.+?)`", r"<font name='Courier'>\1</font>", value)
    for index, link in enumerate(links):
        value = value.replace(f"URLPLACEHOLDER{index}END", link)
    value = re.sub(r"\[((?:S)?\d+)\]", r"<font color='#3467eb'><b>[\1]</b></font>", value)
    return value


def render_pdf(markdown: str, output_path: Path, title: str) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=26, leading=32, textColor=colors.HexColor("#18233A"), alignment=TA_CENTER, spaceAfter=24))
    styles.add(ParagraphStyle(name="H1X", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=23, textColor=colors.HexColor("#18233A"), spaceBefore=16, spaceAfter=9, keepWithNext=True))
    styles.add(ParagraphStyle(name="H2X", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=colors.HexColor("#28456F"), spaceBefore=12, spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.2, leading=15, textColor=colors.HexColor("#253047"), spaceAfter=8, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="ExecutiveX", parent=styles["BodyX"], fontSize=9.8, leading=13.6, spaceAfter=7))
    styles.add(ParagraphStyle(name="BulletX", parent=styles["BodyX"], leftIndent=18, firstLineIndent=-9, bulletIndent=6, spaceAfter=5))
    styles.add(ParagraphStyle(name="SourceX", parent=styles["BodyX"], fontSize=8.5, leading=12, textColor=colors.HexColor("#4B5870")))

    story = [Spacer(1, 1.2 * inch), Paragraph(_inline_markup(title), styles["CoverTitle"]), Spacer(1, 0.35 * inch), Paragraph("Deep Research Report", styles["Heading2"]), Spacer(1, 4.5 * inch), Paragraph("Generated by the Local-First Asynchronous Deep Research Harness", styles["SourceX"]), PageBreak()]

    bibliography = False
    current_section = ""
    page_break_headings = {
        "research question and scope",
        "full research report",
        "bibliography",
        "collection notes",
        "independent evidence and citation audit",
    }
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        if line.startswith("# "):
            heading = line[2:].strip()
            normalized_heading = heading.lower()
            if normalized_heading in page_break_headings:
                story.append(PageBreak())
            current_section = normalized_heading
            bibliography = heading.lower().startswith("bibliography")
            story.append(Paragraph(_inline_markup(heading), styles["H1X"]))
        elif line.startswith("## "):
            heading = line[3:].strip()
            bibliography = heading.lower().startswith("bibliography")
            story.append(Paragraph(_inline_markup(heading), styles["H2X"]))
        elif line.startswith("### "):
            story.append(Paragraph(_inline_markup(line[4:].strip()), styles["Heading3"]))
        elif re.match(r"^[-*]\s+", line):
            story.append(Paragraph(_inline_markup(line[2:].strip()), styles["BulletX"], bulletText="•"))
        elif re.match(r"^\d+[.)]\s+", line):
            number, content = re.split(r"[.)]\s+", line, maxsplit=1)
            story.append(Paragraph(_inline_markup(content), styles["BulletX"], bulletText=f"{number}."))
        else:
            if bibliography or re.match(r"^\[(?:S)?\d+\]", line):
                style = styles["SourceX"]
            elif current_section == "executive summary":
                style = styles["ExecutiveX"]
            else:
                style = styles["BodyX"]
            story.append(Paragraph(_inline_markup(line), style))

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D8DFEA"))
        canvas.line(0.7 * inch, 0.58 * inch, 7.8 * inch, 0.58 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6D788C"))
        canvas.drawString(0.7 * inch, 0.36 * inch, "Local Deep Research")
        canvas.drawRightString(7.8 * inch, 0.36 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter, rightMargin=0.7 * inch, leftMargin=0.7 * inch,
        topMargin=0.72 * inch, bottomMargin=0.72 * inch,
        title=title, author="Local Deep Research Harness",
    )
    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return len(PdfReader(str(output_path)).pages)
