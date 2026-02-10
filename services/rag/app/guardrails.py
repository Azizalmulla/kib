import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .schemas import StrictRagResponse

LLM_SYSTEM_PROMPT_BASE = (
    "You are the KIB Knowledge Copilot. Answer ONLY using the provided chunks. "
    "If the chunks do not contain enough evidence, refuse with the exact message: "
    "\"I can't answer from KIB's approved documents for this question.\" "
    "Do NOT use general knowledge. Do NOT fabricate policies, numbers, fees, or limits. "
    "Return JSON that matches the response schema exactly. "
    "Every non-refusal answer must include citations derived from the provided chunks only."
)

ROLE_PROMPT_OVERRIDES = {
    "front_desk": (
        " Keep answers concise (2-4 sentences). Focus on the key facts the employee "
        "needs to help a customer quickly: amounts, deadlines, steps, or eligibility. "
        "Use simple, clear language. Avoid legal jargon."
    ),
    "compliance": (
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


def _find_matching_row(
    citation: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find the best matching row for an LLM-generated citation.

    Match by doc_id + page_number first (most reliable from LLM).
    Falls back to doc_id only if page doesn't match."""
    cit_doc_id = str(citation.get("doc_id", ""))
    cit_page = citation.get("page_number")

    # Try doc_id + page_number match
    for row in rows:
        if str(row.get("document_id")) == cit_doc_id and row.get("page_start") == cit_page:
            return row

    # Fall back to doc_id only
    for row in rows:
        if str(row.get("document_id")) == cit_doc_id:
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
