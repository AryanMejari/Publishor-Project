"""
Research Paper Evaluator – Streamlit App
---------------------------------------

Features
- Upload a research paper (PDF/TXT)
- Optional: upload reference papers (PDF/TXT, multiple) for local plagiarism/overlap check
- Generates:
    * Executive overview (AI summary)
    * Readability metrics (Flesch Reading Ease, FK Grade, SMOG, Gunning Fog, etc.)
    * Structure & citation analysis (section presence, references, figures/tables, citation recency)
    * Plagiarism/overlap assessment via character n‑gram TF‑IDF cosine similarity and word 5‑gram overlap
    * Final weighted verdict with reasons (emphasis on plagiarism + readability + overview)
- Downloadable JSON report

Quick start
-----------
1) Create and activate a virtual environment (recommended).
2) Install requirements:
   pip install streamlit pdfplumber textstat scikit-learn nltk numpy transformers torch regex
3) (Optional but recommended) On first run NLTK will download tokenizers automatically.
4) Run the app:
   streamlit run app.py

Notes
- Plagiarism detection here is local (against uploaded reference docs) and heuristic. For official checks, use tools like Turnitin/iThenticate. This tool is for educational/pre‑screening use.
- The summarizer uses HuggingFace transformers. If model download is slow or blocked, the app falls back to an extractive frequency‑based summary.
"""

import io
import json
import math
import os
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import streamlit as st

# --- Optional heavy deps (transformers) with graceful fallback ---
_SUMMARIZER_PIPELINE = None
try:
    from transformers import pipeline
    _SUMMARIZER_PIPELINE = pipeline(
        "summarization", model="sshleifer/distilbart-cnn-12-6", device_map="auto"
    )
except Exception:
    _SUMMARIZER_PIPELINE = None

# --- Other deps ---
import pdfplumber
import textstat
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# NLTK setup
import nltk
try:
    nltk.data.find("tokenizers/punkt")
except LookupError:  # download once
    nltk.download("punkt")
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    try:
        nltk.download("punkt_tab")
    except Exception:
        pass
try:
    nltk.data.find("corpora/stopwords")
except LookupError:
    nltk.download("stopwords")

from nltk.tokenize import sent_tokenize, word_tokenize
from nltk.corpus import stopwords

STOPWORDS = set(stopwords.words("english"))
CURRENT_YEAR = datetime.now().year

# ------------------------- Utils -------------------------

def normalize_ws(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_text_from_pdf_bytes(data: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return normalize_ws("\n".join(pages))
    except Exception:
        return ""


def extract_text_from_file(uploaded) -> str:
    name = uploaded.name.lower()
    data = uploaded.read()
    if name.endswith(".pdf"):
        return extract_text_from_pdf_bytes(data)
    elif name.endswith(".txt"):
        try:
            return normalize_ws(data.decode("utf-8", errors="ignore"))
        except Exception:
            return normalize_ws(data.decode("latin-1", errors="ignore"))
    else:
        return ""


SECTION_PATTERNS = [
    ("abstract", r"\babstract\b"),
    ("introduction", r"\bintroduction\b"),
    ("related_work", r"\brelated\s+work\b|\bliterature\s+review\b"),
    ("methods", r"\bmethod(?:s|ology)?\b|\bmaterials\s+and\s+methods\b"),
    ("results", r"\bresults?\b|\bfindings\b"),
    ("discussion", r"\bdiscussion\b"),
    ("conclusion", r"\bconclusion(?:s)?\b"),
    ("references", r"\breferences\b|\bbibliograph\w*\b|\bworks\s+cited\b"),
]


def detect_sections(text: str) -> Dict[str, Tuple[int, int]]:
    """Return {section_name: (start_idx, end_idx)} using heading keyword heuristics."""
    text_l = text.lower()
    matches = []
    for name, pat in SECTION_PATTERNS:
        for m in re.finditer(pat, text_l):
            matches.append((m.start(), name))
    matches.sort()

    spans = {}
    for i, (start, name) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        # only keep first occurrence per section name
        if name not in spans:
            spans[name] = (start, end)
    return spans


def slice_text(text: str, span: Tuple[int, int]) -> str:
    s, e = span
    return text[s:e].strip()


# --------------------- Readability ----------------------

def readability_metrics(text: str) -> Dict[str, float]:
    # Textstat expects raw text; guard against very short text
    t = text if len(text.split()) >= 50 else (text * 3)
    metrics = {
        "flesch_reading_ease": textstat.flesch_reading_ease(t),
        "flesch_kincaid_grade": textstat.flesch_kincaid_grade(t),
        "smog_index": textstat.smog_index(t),
        "gunning_fog": textstat.gunning_fog(t),
        "dale_chall_score": textstat.dale_chall_readability_score(t),
        "automated_readability_index": textstat.automated_readability_index(t),
        "avg_sentence_length": textstat.avg_sentence_length(t),
    }
    # Normalize readability to [0,1] using Flesch Reading Ease (30->0, 80->1)
    fre = metrics["flesch_reading_ease"]
    norm = (fre - 30) / 50
    metrics["readability_norm"] = float(max(0, min(1, norm)))
    return metrics


# ---------------------- Summary -------------------------

def chunk_text(text: str, max_chars: int = 2500) -> List[str]:
    chunks, buf = [], []
    count = 0
    for sent in sent_tokenize(text):
        if count + len(sent) > max_chars and buf:
            chunks.append(" ".join(buf))
            buf, count = [], 0
        buf.append(sent)
        count += len(sent)
    if buf:
        chunks.append(" ".join(buf))
    return chunks if chunks else [text]


def abstractive_summary(text: str, target_words: int = 200) -> str:
    if not _SUMMARIZER_PIPELINE:
        return ""
    parts = chunk_text(text)
    out = []
    for p in parts[:6]:  # cap to avoid excessive calls
        try:
            # distilbart expects shorter inputs
            res = _SUMMARIZER_PIPELINE(p[:3000], max_length=220, min_length=80, do_sample=False)
            out.append(res[0]["summary_text"]) if res else None
        except Exception:
            continue
    summary = " ".join(out)
    # Trim to target words
    words = summary.split()
    if len(words) > target_words:
        summary = " ".join(words[:target_words])
    return summary


def extractive_summary(text: str, target_words: int = 200) -> str:
    sents = sent_tokenize(text)
    if not sents:
        return ""
    words = [w.lower() for w in word_tokenize(text) if w.isalpha() and w.lower() not in STOPWORDS]
    freq = Counter(words)
    scores = []
    for s in sents:
        sw = [w.lower() for w in word_tokenize(s) if w.isalpha()]
        score = sum(freq.get(w, 0) for w in sw) / (len(sw) + 1e-6)
        scores.append((score, s))
    scores.sort(reverse=True, key=lambda x: x[0])
    picked, total = [], 0
    for _, s in scores:
        picked.append(s)
        total += len(s.split())
        if total >= target_words:
            break
    return normalize_ws(" ".join(picked))


def make_overview(text: str) -> str:
    # Try abstractive; fallback to extractive
    summary = abstractive_summary(text, target_words=200)
    if not summary:
        summary = extractive_summary(text, target_words=200)
    return summary


# -------------------- Plagiarism ------------------------

def word_ngrams(tokens: List[str], n: int = 5) -> Counter:
    grams = Counter()
    for i in range(len(tokens) - n + 1):
        grams[tuple(tokens[i:i+n])] += 1
    return grams


def tokenize_words(text: str) -> List[str]:
    return [w.lower() for w in word_tokenize(text) if re.match(r"[A-Za-z]+$", w)]


def self_repetition_ratio(text: str) -> float:
    sents = [s.strip().lower() for s in sent_tokenize(text)]
    long_sents = [s for s in sents if len(s.split()) >= 12]
    c = Counter(long_sents)
    repeated = sum(cnt for s, cnt in c.items() if cnt > 1)
    total = len(long_sents) if long_sents else 1
    return repeated / total


def char_ngram_cosine(main_text: str, corpus_texts: List[str]) -> List[Tuple[int, float]]:
    if not corpus_texts:
        return []
    docs = [main_text] + corpus_texts
    vec = TfidfVectorizer(analyzer="char", ngram_range=(5,7), min_df=1)
    X = vec.fit_transform(docs)
    sims = cosine_similarity(X[0:1], X[1:]).flatten()
    ranked = sorted(list(enumerate(sims)), key=lambda x: x[1], reverse=True)
    return ranked


def word_5gram_overlap(main_text: str, corpus_texts: List[str]) -> float:
    tokens_main = tokenize_words(main_text)
    grams_main = set(word_ngrams(tokens_main, n=5).keys())
    if not grams_main:
        return 0.0
    max_overlap = 0.0
    for ct in corpus_texts:
        grams_ref = set(word_ngrams(tokenize_words(ct), n=5).keys())
        if not grams_ref:
            continue
        inter = len(grams_main & grams_ref)
        ratio = inter / max(1, len(grams_main))
        max_overlap = max(max_overlap, ratio)
    return float(max_overlap)


def plagiarism_assessment(main_text: str, corpus_texts: List[str]) -> Dict:
    # Character n‑gram cosine similarities
    ranked = char_ngram_cosine(main_text, corpus_texts)
    top_cosine = [(idx, float(score)) for idx, score in (ranked[:5] if ranked else [])]
    max_cos = float(top_cosine[0][1]) if top_cosine else 0.0

    # Word 5‑gram overlap
    overlap_ratio = word_5gram_overlap(main_text, corpus_texts) if corpus_texts else 0.0

    # Self repetition
    repetition = self_repetition_ratio(main_text)

    # Risk heuristic
    risk = "low"
    if max_cos >= 0.75 or overlap_ratio >= 0.20:
        risk = "high"
    elif max_cos >= 0.55 or overlap_ratio >= 0.12:
        risk = "medium"

    return {
        "max_char_ngram_cosine": max_cos,
        "top_char_ngram_cosine": top_cosine,  # indices into corpus list
        "max_word5_overlap_ratio": overlap_ratio,
        "self_repetition_ratio": repetition,
        "risk": risk,
    }


# ----------------- Citations & Structure ----------------
CITATION_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
REF_ITEM_RE = re.compile(r"^(\[?\d+\]?\.|\[\d+\]|•|\-)\s+|\(.*?\d{4}.*?\)")


def structure_and_citations(text: str, spans: Dict[str, Tuple[int, int]]) -> Dict:
    present_sections = {name: (name in spans) for name, _ in SECTION_PATTERNS}

    # Figures/Tables
    figures = len(re.findall(r"\bfigure\s*\d+|\bfig\.\s*\d+", text, flags=re.I))
    tables = len(re.findall(r"\btable\s*\d+", text, flags=re.I))

    # References block
    refs_text = slice_text(text, spans["references"]) if "references" in spans else ""
    ref_lines = [l.strip() for l in refs_text.splitlines() if l.strip()]
    ref_items = sum(1 for l in ref_lines if REF_ITEM_RE.search(l))
    doi_count = len(DOI_RE.findall(refs_text))

    # Citation years across the paper
    years = [int(y) for y in CITATION_YEAR_RE.findall(text)]
    recent_cutoff = CURRENT_YEAR - 5
    recent_ratio = sum(1 for y in years if y >= recent_cutoff) / max(1, len(years))

    # Format hints
    numeric_cites = len(re.findall(r"\[\d+\]", text))
    author_year_cites = len(re.findall(r"\([A-Za-z].*?\d{4}[a-z]?\)", text))

    return {
        "sections_present": present_sections,
        "figure_count": figures,
        "table_count": tables,
        "reference_items": ref_items,
        "doi_in_references": doi_count,
        "citation_years_count": len(years),
        "recent_citation_ratio": float(recent_ratio),
        "numeric_citations_detected": numeric_cites,
        "author_year_citations_detected": author_year_cites,
    }


# ------------------ Scoring & Verdict -------------------

def compute_scores(readability: Dict, plag: Dict, struct: Dict) -> Dict:
    # Readability
    read_norm = readability.get("readability_norm", 0.0)

    # Plagiarism uniqueness score (1 = unique, 0 = highly overlapping)
    if plag:
        uniqueness = 1.0 - max(plag.get("max_char_ngram_cosine", 0.0), plag.get("max_word5_overlap_ratio", 0.0))
        uniqueness = float(max(0.0, min(1.0, uniqueness)))
    else:
        uniqueness = 0.5  # unknown corpus => neutral

    # Structure score: fraction of key sections + references presence + figs/tables bonus
    key_sections = ["abstract", "introduction", "methods", "results", "discussion", "conclusion", "references"]
    present = struct.get("sections_present", {})
    section_frac = sum(1 for k in key_sections if present.get(k, False)) / len(key_sections)
    refs_bonus = 1.0 if struct.get("reference_items", 0) >= 10 else 0.0
    figs_tables_bonus = 1.0 if (struct.get("figure_count", 0) + struct.get("table_count", 0)) >= 2 else 0.0
    recency = struct.get("recent_citation_ratio", 0.0)
    structure_score = 0.6 * section_frac + 0.2 * refs_bonus + 0.2 * figs_tables_bonus

    # Weighted overall
    weights = {"readability": 0.2, "plagiarism": 0.4, "structure": 0.4}
    overall = (
        weights["readability"] * read_norm +
        weights["plagiarism"] * uniqueness +
        weights["structure"] * (0.7 * structure_score + 0.3 * recency)
    )

    # Verdict logic emphasizing plagiarism and readability
    if plag and plag.get("risk") == "high":
        verdict = "REJECT – High plagiarism/overlap risk"
    elif read_norm < 0.3:
        verdict = "MAJOR REVISIONS – Poor readability"
    else:
        if overall >= 0.80:
            verdict = "ACCEPT"
        elif overall >= 0.65:
            verdict = "MINOR REVISIONS"
        elif overall >= 0.50:
            verdict = "MAJOR REVISIONS"
        else:
            verdict = "REJECT"

    return {
        "overall_score": float(round(overall, 3)),
        "readability_norm": float(round(read_norm, 3)),
        "uniqueness_score": float(round(uniqueness, 3)),
        "structure_score": float(round(structure_score, 3)),
        "recency_ratio": float(round(recency, 3)),
        "verdict": verdict,
    }


# --------------------- Streamlit UI ---------------------
st.set_page_config(page_title="Research Paper Evaluator", layout="wide")
st.title("📄 Research Paper Evaluator (AI‑assisted)")
st.caption("Educational pre‑screening tool. Emphasis on plagiarism, readability, and overview.")

with st.sidebar:
    st.header("Upload")
    main_file = st.file_uploader("Research paper (PDF or TXT)", type=["pdf", "txt"], accept_multiple_files=False)
    corpus_files = st.file_uploader(
        "Optional reference docs for plagiarism/overlap check (PDF/TXT, multiple)",
        type=["pdf", "txt"], accept_multiple_files=True
    )
    target_summary_words = st.slider("Overview length (words)", 120, 400, 200, 10)
    run_btn = st.button("Evaluate Paper", type="primary", use_container_width=True)

if run_btn:
    if not main_file:
        st.error("Please upload a research paper.")
        st.stop()

    # Read main text
    main_text = extract_text_from_file(main_file)
    if not main_text or len(main_text.split()) < 100:
        st.error("Couldn't read enough text from the uploaded paper. Ensure it's a selectable‑text PDF or upload a TXT.")
        st.stop()

    # Read corpus texts
    corpus_texts, corpus_names = [], []
    for f in (corpus_files or []):
        txt = extract_text_from_file(f)
        if txt:
            corpus_texts.append(txt)
            corpus_names.append(f.name)

    st.subheader("Final Verdict")
    with st.spinner("Analyzing…"):
        # Sections
        spans = detect_sections(main_text)

        # Overview
        overview = make_overview(main_text)

        # Readability
        read = readability_metrics(main_text)

        # Structure & citations
        struct = structure_and_citations(main_text, spans)

        # Plagiarism
        plag = plagiarism_assessment(main_text, corpus_texts) if corpus_texts else {
            "max_char_ngram_cosine": 0.0,
            "top_char_ngram_cosine": [],
            "max_word5_overlap_ratio": 0.0,
            "self_repetition_ratio": self_repetition_ratio(main_text),
            "risk": "low",
        }

        # Scores & verdict
        scores = compute_scores(read, plag, struct)

    # Verdict banner
    v = scores["verdict"]
    if v.startswith("ACCEPT"):
        st.success(f"{v} — Overall {scores['overall_score']}")
    elif v.startswith("MINOR"):
        st.info(f"{v} — Overall {scores['overall_score']}")
    elif v.startswith("MAJOR"):
        st.warning(f"{v} — Overall {scores['overall_score']}")
    else:
        st.error(f"{v} — Overall {scores['overall_score']}")

    # --- Overview ---
    st.markdown("### 🧭 Executive Overview")
    st.write(overview if overview else "(Overview unavailable)")

    # --- Readability ---
    st.markdown("### ✍️ Readability Metrics")
    cols = st.columns(3)
    with cols[0]:
        st.metric("Flesch Reading Ease", round(read["flesch_reading_ease"], 2))
        st.metric("FK Grade", round(read["flesch_kincaid_grade"], 2))
        st.metric("Gunning Fog", round(read["gunning_fog"], 2))
    with cols[1]:
        st.metric("SMOG", round(read["smog_index"], 2))
        st.metric("Dale‑Chall", round(read["dale_chall_score"], 2))
        st.metric("ARI", round(read["automated_readability_index"], 2))
    with cols[2]:
        st.metric("Avg sentence length", round(read["avg_sentence_length"], 2))
        st.metric("Readability (0‑1)", round(read["readability_norm"], 3))

    # --- Structure & citations ---
    st.markdown("### 🧱 Structure & Citations")
    sp = struct["sections_present"]
    sec_cols = st.columns(4)
    for i, key in enumerate(["abstract", "introduction", "methods", "results", "discussion", "conclusion", "references"]):
        with sec_cols[i % 4]:
            st.checkbox(key.capitalize(), value=sp.get(key, False), disabled=True)
    st.write(
        f"Figures: **{struct['figure_count']}**, Tables: **{struct['table_count']}** | Reference items: **{struct['reference_items']}**, DOIs in refs: **{struct['doi_in_references']}**"
    )
    st.write(
        f"Citations detected: years **{struct['citation_years_count']}**, recent (≥{CURRENT_YEAR-5}) ratio **{round(struct['recent_citation_ratio'], 2)}**, "
        f"numeric style cites **{struct['numeric_citations_detected']}**, author‑year cites **{struct['author_year_citations_detected']}**"
    )

    # --- Plagiarism/overlap ---
    st.markdown("### 🔍 Plagiarism / Overlap Assessment")
    if corpus_texts:
        st.write(
            f"Max char n‑gram cosine: **{round(plag['max_char_ngram_cosine'], 3)}**, Max word 5‑gram overlap: **{round(plag['max_word5_overlap_ratio'], 3)}**, "
            f"Self‑repetition ratio: **{round(plag['self_repetition_ratio'], 3)}**, Risk: **{plag['risk'].upper()}**"
        )
        if plag["top_char_ngram_cosine"]:
            st.write("Top reference matches:")
            rows = []
            for idx, score in plag["top_char_ngram_cosine"]:
                name = corpus_names[idx] if 0 <= idx < len(corpus_names) else f"Doc {idx+1}"
                rows.append({"Reference": name, "Cosine": round(score, 3)})
            st.dataframe(rows, use_container_width=True)
    else:
        st.info("No reference corpus uploaded. Overlap risk is estimated without external documents. For stronger plagiarism checks, upload reference PDFs/TXTs.")
        st.write(
            f"Self‑repetition ratio: **{round(plag['self_repetition_ratio'], 3)}** (higher can indicate boilerplate or redundancy)"
        )

    # --- Scores ---
    st.markdown("### 📊 Scores (Weighted)")
    st.write(
        f"Readability: **{scores['readability_norm']}**, Uniqueness: **{scores['uniqueness_score']}**, "
        f"Structure: **{scores['structure_score']}**, Recency: **{scores['recency_ratio']}**"
    )

    # --- Download report ---
    report = {
        "verdict": scores["verdict"],
        "overall_score": scores["overall_score"],
        "readability": read,
        "structure_citations": struct,
        "plagiarism": plag,
        "overview": overview,
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "app": "Research Paper Evaluator",
            "version": "1.0",
        },
    }
    buff = io.BytesIO()
    buff.write(json.dumps(report, indent=2).encode("utf-8"))
    st.download_button(
        label="⬇️ Download JSON Report",
        data=buff.getvalue(),
        file_name="paper_evaluation_report.json",
        mime="application/json",
    )

    # --- Recommendations ---
    st.markdown("### 🛠️ Recommendations (Auto‑generated)")
    recs = []
    # Plagiarism risk
    if plag.get("risk") == "high":
        recs.append("High overlap detected with the reference corpus. Rephrase affected sections, cite properly, and ensure original contributions are clearly distinguished.")
    elif plag.get("risk") == "medium":
        recs.append("Moderate overlap detected. Review similar passages and improve paraphrasing and citation clarity.")

    # Readability
    if read["readability_norm"] < 0.5:
        recs.append("Improve readability: shorten sentences, reduce jargon, and add transitional phrases for flow.")

    # Structure
    missing = [k for k, v in sp.items() if k in {"abstract", "introduction", "methods", "results", "discussion", "conclusion", "references"} and not v]
    if missing:
        recs.append(f"Missing or unclear sections: {', '.join(missing)}. Ensure standard IMRaD structure is present and labeled.")
    if struct["reference_items"] < 10:
        recs.append("Increase and diversify references; aim for at least 10 quality citations including recent work.")
    if struct["recent_citation_ratio"] < 0.3:
        recs.append("Cite more recent literature from the last 5 years to strengthen currency of work.")
    if (struct["figure_count"] + struct["table_count"]) < 2:
        recs.append("Add informative figures/tables to illustrate methodology or results.")

    if not recs:
        recs.append("Paper appears well‑structured with acceptable readability and low overlap based on provided corpus.")
    for r in recs:
        st.write("• " + r)
