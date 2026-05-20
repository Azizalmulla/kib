import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .schemas import StrictRagResponse

LLM_SYSTEM_PROMPT_BASE = (
    "You are the KIB Knowledge Copilot. Answer ONLY using the provided KIB evidence excerpts. "
    "If the evidence excerpts do not contain enough evidence, refuse with the exact message: "
    "\"I can't answer from KIB's approved documents for this question.\" "
    "Do NOT use general knowledge. Do NOT fabricate policies, numbers, fees, or limits. "
    "Write like a polished KIB WhatsApp assistant: direct, useful, and professional. "
    "Never mention internal retrieval terms such as chunks, context, retrieved context, or provided documents. "
    "Return JSON that matches the response schema exactly. "
    "Every non-refusal answer must include citations derived from the provided evidence excerpts only."
)

ROLE_PROMPT_OVERRIDES = {
    "employee": (
        " Provide clear, helpful answers in 3-6 sentences. Focus on the key facts: "
        "amounts, deadlines, steps, eligibility, or requirements. "
        "Use professional but accessible language."
    ),
    "admin": (
        " Provide thorough, detailed answers. Include all relevant clauses, conditions, "
        "exceptions, and regulatory references. Quote exact policy language where possible. "
        "Cite every chunk that contributed to the answer. Err on the side of completeness."
    ),
}


def get_system_prompt(role_names: list) -> str:
    """Return a role-tailored system prompt."""
    prompt = LLM_SYSTEM_PROMPT_BASE
    for role in role_names:
        override = ROLE_PROMPT_OVERRIDES.get(role)
        if override:
            prompt += override
            break
    return prompt

REFUSAL_TEXT_EN = "I can't answer from KIB's approved documents for this question."
REFUSAL_TEXT_AR = "لا أستطيع الإجابة من مستندات KIB المعتمدة لهذا السؤال."

MISSING_INFO_EN = (
    "No approved documents matched this question, or the evidence was too weak. "
    "This may be outside the KIB knowledge base. "
    "Try adding the policy name, product name, or section title."
)
MISSING_INFO_AR = (
    "لا توجد مستندات معتمدة مطابقة لهذا السؤال، أو أن الأدلة ضعيفة. "
    "قد يكون هذا خارج نطاق قاعدة معرفة KIB. "
    "جرّب إضافة اسم السياسة أو المنتج أو عنوان القسم."
)

ANSWER_FAILED_INFO_EN = (
    "The answer model is temporarily unavailable. Please retry shortly."
)
ANSWER_FAILED_INFO_AR = (
    "نموذج توليد الإجابة غير متاح مؤقتاً. يرجى إعادة المحاولة بعد قليل."
)


def build_answer_failed_payload(language: str) -> Dict[str, Any]:
    language = "ar" if language == "ar" else "en"
    return {
        "language": language,
        "answer": REFUSAL_TEXT_AR if language == "ar" else REFUSAL_TEXT_EN,
        "confidence": "low",
        "citations": [],
        "missing_info": ANSWER_FAILED_INFO_AR if language == "ar" else ANSWER_FAILED_INFO_EN,
        "safe_next_steps": safe_next_steps(language),
    }

SAFE_NEXT_STEPS_EN = [
    "Search by policy or product name.",
    "Include the document section or clause title.",
    "Ask about a specific form, fee, or limit.",
]

SAFE_NEXT_STEPS_AR = [
    "ابحث باسم السياسة أو المنتج.",
    "اذكر عنوان القسم أو البند في المستند.",
    "اسأل عن نموذج أو رسوم أو حد محدد.",
]


def safe_next_steps(language: str) -> List[str]:
    steps = SAFE_NEXT_STEPS_AR if language == "ar" else SAFE_NEXT_STEPS_EN
    return steps[:3]


def _truncate_words(text: str, max_words: int) -> str:
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words])


def _quote_snippet(text: str) -> str:
    return _truncate_words(text.replace("\n", " ").strip(), 25)


def build_refusal_payload(language: str) -> Dict[str, Any]:
    language = "ar" if language == "ar" else "en"
    answer = REFUSAL_TEXT_AR if language == "ar" else REFUSAL_TEXT_EN
    return {
        "language": language,
        "answer": answer,
        "confidence": "low",
        "citations": [],
        "missing_info": translate_missing_info("low", language),
        "safe_next_steps": safe_next_steps(language),
    }


def compute_confidence(rows: List[Dict[str, Any]], citations: List[Dict[str, Any]]) -> str:
    if not citations:
        return "low"

    sims: List[float] = []
    for row in rows:
        dist = row.get("distance")
        if dist is None:
            continue
        sim = 1.0 - float(dist)
        if sim < 0:
            sim = 0.0
        if sim > 1:
            sim = 1.0
        sims.append(sim)

    avg_sim = sum(sims) / len(sims) if sims else 0.0

    if len(citations) >= 2 and avg_sim >= 0.7:
        return "high"
    if len(citations) >= 1 and avg_sim >= 0.55:
        return "medium"
    return "low"


def translate_missing_info(confidence: str, language: str) -> Optional[str]:
    if confidence != "low":
        return None
    if language == "ar":
        return MISSING_INFO_AR
    return MISSING_INFO_EN


def _normalize_id(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _quote_overlap(citation: Dict[str, Any], row: Dict[str, Any]) -> bool:
    quote = str(citation.get("quote") or "").strip()
    if len(quote) < 12:
        return False
    text = str(row.get("text") or "")
    if not text:
        return False
    snippet = quote[:60].lower()
    return snippet in text.lower()


def _find_matching_row(
    citation: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find the best matching row for an LLM-generated citation.

    Match strictly by doc_id + page first, then doc_id only, then a
    normalized id match (handles dashes/case), then quote overlap.
    """
    cit_doc_id = str(citation.get("doc_id", ""))
    cit_page = citation.get("page_number")

    for row in rows:
        if str(row.get("document_id")) == cit_doc_id and row.get("page_start") == cit_page:
            return row

    for row in rows:
        if str(row.get("document_id")) == cit_doc_id:
            return row

    cit_doc_norm = _normalize_id(cit_doc_id)
    if cit_doc_norm:
        for row in rows:
            if _normalize_id(row.get("document_id")) == cit_doc_norm:
                return row

    for row in rows:
        if _quote_overlap(citation, row):
            return row

    return None


def normalize_citations(
    citations: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    normalized: List[Dict[str, Any]] = []
    used_rows: List[Dict[str, Any]] = []
    seen_doc_pages: set = set()

    for citation in citations:
        row = _find_matching_row(citation, rows)
        if row is None:
            continue
        dedup_key = (str(row.get("document_id")), row.get("page_start"))
        if dedup_key in seen_doc_pages:
            continue
        seen_doc_pages.add(dedup_key)
        used_rows.append(row)
        normalized.append(
            {
                "doc_title": row.get("document_title"),
                "doc_id": str(row.get("document_id")),
                "document_version": row.get("document_version"),
                "page_number": row.get("page_start"),
                "start_offset": row.get("offset_start"),
                "end_offset": row.get("offset_end"),
                "quote": _quote_snippet(citation.get("quote") or row.get("text", "")),
                "source_uri": row.get("source_uri"),
            }
        )

    return normalized, used_rows


def validate_or_refuse(payload: Dict[str, Any], language: str) -> Dict[str, Any]:
    language = "ar" if language == "ar" else "en"
    try:
        StrictRagResponse(**payload)
        return payload
    except ValidationError:
        fallback = build_refusal_payload(language)
        StrictRagResponse(**fallback)
        return fallback


def build_meta(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    trace_id = str(uuid.uuid4())
    return {
        "trace_id": trace_id,
        "retrieved_chunk_ids": [str(row["chunk_id"]) for row in rows],
    }
