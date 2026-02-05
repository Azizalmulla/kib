import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .schemas import StrictRagResponse

LLM_SYSTEM_PROMPT = (
    "You are the KIB Knowledge Copilot. Answer ONLY using the provided chunks. "
    "If the chunks do not contain enough evidence, refuse with the exact message: "
    "\"I can't answer from KIB's approved documents for this question.\" "
    "Do NOT use general knowledge. Do NOT fabricate policies, numbers, fees, or limits. "
    "Return JSON that matches the response schema exactly. "
    "Every non-refusal answer must include citations derived from the provided chunks only."
)

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


def _citation_key(citation: Dict[str, Any]) -> Optional[Tuple[str, str, Optional[int], Optional[int], Optional[int], str]]:
    try:
        return (
            str(citation.get("doc_id")),
            str(citation.get("document_version")),
            citation.get("page_number"),
            citation.get("start_offset"),
            citation.get("end_offset"),
            str(citation.get("source_uri")),
        )
    except Exception:
        return None


def _row_key(row: Dict[str, Any]) -> Tuple[str, str, Optional[int], Optional[int], Optional[int], str]:
    return (
        str(row.get("document_id")),
        str(row.get("document_version")),
        row.get("page_start"),
        row.get("offset_start"),
        row.get("offset_end"),
        str(row.get("source_uri")),
    )


def normalize_citations(
    citations: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    row_map = {_row_key(row): row for row in rows}
    normalized: List[Dict[str, Any]] = []
    used_rows: List[Dict[str, Any]] = []

    for citation in citations:
        key = _citation_key(citation)
        if key is None or key not in row_map:
            return [], []
        row = row_map[key]
        used_rows.append(row)
        normalized.append(
            {
                "doc_title": row.get("document_title"),
                "doc_id": str(row.get("document_id")),
                "document_version": row.get("document_version"),
                "page_number": row.get("page_start"),
                "start_offset": row.get("offset_start"),
                "end_offset": row.get("offset_end"),
                "quote": _quote_snippet(row.get("text", "")),
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
