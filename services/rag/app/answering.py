import json
from typing import Any, Dict, List, Tuple

from .guardrails import (
    LLM_SYSTEM_PROMPT,
    REFUSAL_TEXT_AR,
    REFUSAL_TEXT_EN,
    build_refusal_payload,
    compute_confidence,
    normalize_citations,
    safe_next_steps,
    translate_missing_info,
    validate_or_refuse,
)
from .llm import LLMProvider


def _build_user_prompt(
    question: str,
    language: str,
    role_names: List[str],
    rows: List[Dict[str, Any]],
) -> str:
    role_list = ", ".join(role_names) if role_names else "none"

    chunks = []
    for idx, row in enumerate(rows, start=1):
        chunks.append(
            "\n".join(
                [
                    f"Chunk {idx}:",
                    f"chunk_id: {row.get('chunk_id')}",
                    f"doc_title: {row.get('document_title')}",
                    f"doc_id: {row.get('document_id')}",
                    f"document_version: {row.get('document_version')}",
                    f"page_number: {row.get('page_start')}",
                    f"start_offset: {row.get('offset_start')}",
                    f"end_offset: {row.get('offset_end')}",
                    f"source_uri: {row.get('source_uri')}",
                    "text:",
                    row.get("text", ""),
                ]
            )
        )

    chunks_block = "\n\n".join(chunks)

    schema_example = '''{
  "answer": "Your answer here based only on the chunks.",
  "citations": [
    {
      "doc_id": "<exact doc_id from chunk>",
      "document_version": "<exact document_version from chunk>",
      "page_number": <exact page_number from chunk>,
      "start_offset": <exact start_offset from chunk>,
      "end_offset": <exact end_offset from chunk>,
      "source_uri": "<exact source_uri from chunk>",
      "quote": "<exact snippet from chunk text, max 25 words>"
    }
  ]
}'''

    return "\n".join(
        [
            "You MUST answer using ONLY the chunks below.",
            "If the chunks are insufficient, return the refusal message exactly.",
            "Return ONLY valid JSON matching the EXACT schema below. No other fields allowed.",
            "Use the same language as the user for the answer.",
            "Each citation must use the EXACT values from the chunk metadata (doc_id, document_version, page_number, start_offset, end_offset, source_uri).",
            "The quote must be an exact snippet from the chunk text, max 25 words, NOT translated.",
            "",
            "REQUIRED JSON SCHEMA:",
            schema_example,
            "",
            f"User language: {language}",
            f"User roles: {role_list}",
            f"User question: {question}",
            "",
            "Retrieved chunks:",
            chunks_block,
        ]
    )


def answer_with_llm(
    rows: List[Dict[str, Any]],
    question: str,
    language: str,
    role_names: List[str],
    provider: LLMProvider,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    language = "ar" if language == "ar" else "en"
    meta = {
        "retrieved_chunk_ids": [str(row["chunk_id"]) for row in rows],
    }

    if not rows:
        return build_refusal_payload(language), meta

    prompt = _build_user_prompt(question, language, role_names, rows)
    try:
        raw = provider.generate(LLM_SYSTEM_PROMPT, prompt)
    except Exception:
        return build_refusal_payload(language), meta

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return build_refusal_payload(language), meta

    if not isinstance(data, dict):
        return build_refusal_payload(language), meta

    answer = str(data.get("answer", "")).strip()
    if answer in {REFUSAL_TEXT_EN, REFUSAL_TEXT_AR}:
        return build_refusal_payload(language), meta

    citations = data.get("citations")
    if not isinstance(citations, list) or not citations:
        return build_refusal_payload(language), meta

    normalized_citations, used_rows = normalize_citations(citations, rows)
    if not normalized_citations:
        return build_refusal_payload(language), meta

    if not answer:
        return build_refusal_payload(language), meta

    confidence = compute_confidence(used_rows, normalized_citations)
    missing_info = translate_missing_info(confidence, language)

    payload = {
        "language": language,
        "answer": answer,
        "confidence": confidence,
        "citations": normalized_citations,
        "missing_info": missing_info,
        "safe_next_steps": safe_next_steps(language),
    }

    payload = validate_or_refuse(payload, language)
    return payload, meta
