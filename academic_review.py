import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from firebase_admin import firestore as admin_firestore

from firebase_store import get_firestore_client, get_paper_by_id, update_paper


BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "review_reports"
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


def run_review(paper_id: str, manuscript_path: str) -> None:
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
        report_text = _report_text(paper, analysis, datetime.now(timezone.utc).isoformat())
        report_hash = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
        REPORTS_DIR.mkdir(exist_ok=True)
        report_path = REPORTS_DIR / f"{paper_id}_screening_report.txt"
        report_path.write_text(report_text, encoding="utf-8")
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
                "review_report_path": str(report_path),
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
