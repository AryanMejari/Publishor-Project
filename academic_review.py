import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from firebase_admin import firestore as admin_firestore
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from firebase_store import get_firestore_client, get_paper_by_id, update_paper
from firebase_storage import upload_bytes


BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = Path(os.getenv("ISIRP_TEMP_DIR", "/tmp")) / "isirp-review-reports"
load_dotenv(BASE_DIR / ".env")

REQUIRED_SECTIONS = {
    "abstract": ("abstract",),
    "introduction": ("introduction", "background"),
    "methodology": ("methodology", "methods", "research design"),
    "results": ("results", "findings", "evaluation"),
    "discussion": ("discussion", "analysis"),
    "conclusion": ("conclusion", "concluding remarks"),
    "references": ("references", "bibliography", "works cited"),
}


def extract_text(file_path: Path) -> str:
    """Extract text from a PDF or DOCX manuscript."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(file_path) as document:
            return "\n".join(page.get_text() for page in document)
    if suffix == ".docx":
        from docx import Document

        document = Document(file_path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise ValueError(f"Unsupported manuscript type: {suffix}")


def _section_presence(text: str) -> Dict[str, bool]:
    lowered = text.lower()
    return {
        section: any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords)
        for section, keywords in REQUIRED_SECTIONS.items()
    }


def _findings(presence: Dict[str, bool], word_count: int, citation_count: int) -> List[str]:
    findings = []
    if word_count < 800:
        findings.append("The manuscript is short for a full research article; expand methods, results, and limitations.")
    for section, present in presence.items():
        if not present:
            findings.append(f"Could not identify a dedicated {section.title()} section.")
    if citation_count < 3:
        findings.append("Fewer than three in-text citations were detected; review the literature support for major claims.")
    return findings


def _gemini_review(text: str, paper: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"status": "not_configured"}

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = f"""You are an academic screening assistant. Analyze this manuscript conservatively.
Return only JSON with these keys:
scores (object with methodology, novelty, statistical_rigor, citation_integrity integers 0-100),
strengths (array of strings), concerns (array of strings),
counter_arguments (array of exactly 3 strings), future_directions (array of exactly 3 strings).
Do not claim plagiarism, misconduct, acceptance, or accreditation. State uncertainty.
Title: {paper.get('paper_title', '')}
Manuscript:
{text[:24000]}"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw = result["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {"status": "invalid_response"}
        parsed.pop("error", None)
        parsed["model"] = model
        parsed["status"] = "completed"
        return parsed
    except (urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "failed", "error": f"Optional AI review unavailable: {exc}"}


def analyze_manuscript(file_path: Path) -> Dict[str, Any]:
    text = extract_text(file_path)
    words = re.findall(r"\b[\w'-]+\b", text)
    citations = re.findall(r"\[[0-9]{1,3}\]|\([A-Z][A-Za-z-]+(?:\s+et al\.)?,?\s+\d{4}\)", text)
    presence = _section_presence(text)
    word_count = len(words)
    citation_count = len(citations)
    structure_score = round(sum(presence.values()) / len(presence) * 100)
    methodology_score = 80 if presence["methodology"] else 35
    citation_score = min(100, 40 + citation_count * 10)
    clarity_score = 80 if word_count >= 1500 else 60 if word_count >= 800 else 35
    overall_score = round((structure_score + methodology_score + citation_score + clarity_score) / 4)

    return {
        "paper_sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
        "word_count": word_count,
        "citation_count": citation_count,
        "section_checks": presence,
        "scores": {
            "structure": structure_score,
            "methodology": methodology_score,
            "citation_support": citation_score,
            "clarity": clarity_score,
            "overall": overall_score,
        },
        "findings": _findings(presence, word_count, citation_count),
        "counter_arguments": [
            "Assess whether an alternative explanation could produce the reported result.",
            "Test whether the conclusion remains stable under a different sample or baseline.",
            "Clarify which assumptions, if relaxed, would materially change the interpretation.",
        ],
        "future_directions": [
            "Replicate the study with a larger or more diverse sample.",
            "Publish the data-processing and evaluation protocol for independent reproduction.",
            "Compare the proposed approach with strong current baselines.",
        ],
        "disclosure": "This is an automated screening report, not accredited peer review.",
    }


def _report_text(paper: Dict[str, Any], analysis: Dict[str, Any], generated_at: str) -> str:
    lines = [
        "ISIRP AUTOMATED ACADEMIC SCREENING REPORT",
        "=" * 48,
        f"Paper ID: {paper.get('paper_id', '')}",
        f"Title: {paper.get('paper_title', '')}",
        f"Author: {paper.get('corresponding_author_name', '')}",
        f"Generated: {generated_at}",
        "",
        "IMPORTANT DISCLOSURE",
        analysis["disclosure"],
        "",
        "SCREENING SCORES",
    ]
    for name, score in analysis["scores"].items():
        lines.append(f"- {name.replace('_', ' ').title()}: {score}/100")
    lines.extend(["", "DOCUMENT METRICS", f"- Word count: {analysis['word_count']:,}", f"- Detected citations: {analysis['citation_count']}", "", "SECTION CHECKS"])
    for section, present in analysis["section_checks"].items():
        lines.append(f"- {section.title()}: {'Present' if present else 'Not detected'}")
    lines.extend(["", "FINDINGS"])
    lines.extend(f"- {finding}" for finding in analysis["findings"])
    lines.extend(["", "COUNTER-ARGUMENTS TO CONSIDER"])
    lines.extend(f"- {item}" for item in analysis["counter_arguments"])
    lines.extend(["", "FUTURE DIRECTIONS"])
    lines.extend(f"- {item}" for item in analysis["future_directions"])
    if analysis.get("ai_review"):
        lines.extend(["", "OPTIONAL AI REVIEW"])
        lines.append(json.dumps(analysis["ai_review"], indent=2, ensure_ascii=False))
    return "\n".join(lines)


def _write_pdf_report(
    path: Path,
    paper: Dict[str, Any],
    analysis: Dict[str, Any],
    generated_at: str,
) -> None:
    """Create a readable PDF version of the screening report."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#172554"),
        fontSize=18,
        leading=23,
        spaceAfter=5 * mm,
    ))
    styles.add(ParagraphStyle(
        name="ReportSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#475569"),
        fontSize=9,
        leading=12,
        spaceAfter=7 * mm,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#1e3a8a"),
        fontSize=12,
        leading=15,
        spaceBefore=5 * mm,
        spaceAfter=2 * mm,
    ))
    styles.add(ParagraphStyle(
        name="BodySmall",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1f2937"),
    ))
    styles.add(ParagraphStyle(
        name="BulletSmall",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        leftIndent=12,
        firstLineIndent=-7,
        textColor=colors.HexColor("#1f2937"),
    ))

    def paragraph(value: Any, style: str = "BodySmall") -> Paragraph:
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    story = [
        Paragraph("ISIRP Automated Academic Screening Report", styles["ReportTitle"]),
        Paragraph(
            "International Scientific and Innovative Research Publications",
            styles["ReportSubtitle"],
        ),
    ]
    metadata = [
        [paragraph("<b>Paper ID</b>"), paragraph(paper.get("paper_id", ""))],
        [paragraph("<b>Title</b>"), paragraph(paper.get("paper_title", ""))],
        [paragraph("<b>Author</b>"), paragraph(paper.get("corresponding_author_name", ""))],
        [paragraph("<b>Generated</b>"), paragraph(generated_at)],
    ]
    metadata_table = Table(metadata, colWidths=[30 * mm, 145 * mm])
    metadata_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2ff")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c7d2fe")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([metadata_table, Spacer(1, 4 * mm)])

    story.extend([
        Paragraph("Important disclosure", styles["SectionHeading"]),
        paragraph(analysis["disclosure"]),
        Paragraph("Screening scores", styles["SectionHeading"]),
    ])
    score_rows = [[paragraph("<b>Metric</b>"), paragraph("<b>Score</b>")]]
    score_rows.extend(
        [paragraph(name.replace("_", " ").title()), paragraph(f"{score}/100")]
        for name, score in analysis["scores"].items()
    )
    score_table = Table(score_rows, colWidths=[130 * mm, 45 * mm])
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172554")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(score_table)

    story.extend([
        Paragraph("Document metrics", styles["SectionHeading"]),
        paragraph(
            f"Word count: {analysis['word_count']:,}<br/>"
            f"Detected citations: {analysis['citation_count']}"
        ),
        Paragraph("Section checks", styles["SectionHeading"]),
    ])
    section_rows = [[paragraph("<b>Section</b>"), paragraph("<b>Status</b>")]]
    section_rows.extend(
        [paragraph(section.title()), paragraph("Present" if present else "Not detected")]
        for section, present in analysis["section_checks"].items()
    )
    section_table = Table(section_rows, colWidths=[130 * mm, 45 * mm])
    section_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172554")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(section_table)

    def add_bullets(title: str, values: List[str]) -> None:
        story.append(Paragraph(title, styles["SectionHeading"]))
        story.append(ListFlowable(
            [ListItem(paragraph(value, "BulletSmall"), bulletColor=colors.HexColor("#1d4ed8"))
             for value in values],
            bulletType="bullet",
            start="circle",
            leftIndent=14,
        ))

    add_bullets("Findings", analysis["findings"])
    add_bullets("Counter-arguments to consider", analysis["counter_arguments"])
    add_bullets("Future directions", analysis["future_directions"])

    if analysis.get("ai_review"):
        story.append(Paragraph("Optional AI review", styles["SectionHeading"]))
        story.append(paragraph(json.dumps(analysis["ai_review"], indent=2, ensure_ascii=False)))

    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"ISIRP Screening Report - {paper.get('paper_id', '')}",
    )
    document.build(story)


def run_review(paper_id: str, manuscript_path: str) -> str:
    """Run local screening and persist results for the existing dashboard."""
    started_at = datetime.now(timezone.utc).isoformat()
    update_paper(paper_id, {"review_status": "running", "review_started_at": started_at})
    try:
        paper = get_paper_by_id(paper_id)
        if not paper:
            raise RuntimeError(f"Paper {paper_id} was not found")
        path = Path(manuscript_path).resolve()
        analysis = analyze_manuscript(path)
        ai_review = _gemini_review(extract_text(path), paper)
        if ai_review.get("status") == "completed":
            analysis["ai_review"] = ai_review
            analysis["counter_arguments"] = ai_review.get("counter_arguments") or analysis["counter_arguments"]
            analysis["future_directions"] = ai_review.get("future_directions") or analysis["future_directions"]
            analysis["findings"].extend(ai_review.get("concerns", [])[:5])
        generated_at = datetime.now(timezone.utc).isoformat()
        report_path = REPORTS_DIR / f"{paper_id}_screening_report.pdf"
        report_path.parent.mkdir(exist_ok=True)
        _write_pdf_report(report_path, paper, analysis, generated_at)
        report_bytes = report_path.read_bytes()
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        report_storage_path = upload_bytes(
            report_bytes,
            f"review-reports/{paper_id}_screening_report.pdf",
            "application/pdf",
        )
        completed_at = datetime.now(timezone.utc).isoformat()
        update_paper(
            paper_id,
            {
                "review_status": "completed",
                "paper_sha256": analysis["paper_sha256"],
                "review_completed_at": completed_at,
                "review_scores": analysis["scores"],
                "review_findings": analysis["findings"],
                "review_section_checks": analysis["section_checks"],
                "review_word_count": analysis["word_count"],
                "review_citation_count": analysis["citation_count"],
                "review_report_path": report_storage_path,
                "review_report_sha256": report_hash,
                "review_disclosure": analysis["disclosure"],
                "review_counter_arguments": analysis["counter_arguments"],
                "review_future_directions": analysis["future_directions"],
                "review_ai": ai_review,
                "review_ai_status": ai_review.get("status", "not_configured"),
            },
        )
        if ai_review.get("status") == "completed":
            get_firestore_client().collection("isirpalldata").document(paper_id).update(
                {"review_ai.error": admin_firestore.DELETE_FIELD}
            )
        return str(report_path)
    except Exception as exc:
        update_paper(
            paper_id,
            {
                "review_status": "failed",
                "review_error": str(exc),
                "review_completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
