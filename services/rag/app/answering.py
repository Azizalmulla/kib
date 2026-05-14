import json
import logging
import re
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from .guardrails import (
    REFUSAL_TEXT_AR,
    REFUSAL_TEXT_EN,
    build_refusal_payload,
    compute_confidence,
    get_system_prompt,
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
    history: List[Tuple[str, str]] = None,
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

    history_block = ""
    if history:
        turns = []
        for role, text in history[-6:]:
            label = "User" if role == "user" else "Assistant"
            turns.append(f"{label}: {text}")
        history_block = "\n".join(["", "Conversation history (for context only, answer the CURRENT question):"] + turns + [""])

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
            history_block,
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
    history: List[Tuple[str, str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    language = "ar" if language == "ar" else "en"
    meta = {
        "retrieved_chunk_ids": [str(row["chunk_id"]) for row in rows],
    }

    if not rows:
        return build_refusal_payload(language), meta

    prompt = _build_user_prompt(question, language, role_names, rows, history=history)
    system_prompt = get_system_prompt(role_names)
    log.debug("[RAG] Sending prompt to LLM (%d chars, %d chunks, roles=%s)", len(prompt), len(rows), role_names)
    try:
        raw = provider.generate(system_prompt, prompt)
    except Exception as exc:
        log.error("[RAG] LLM call failed: %s", exc)
        return build_refusal_payload(language), meta

    log.debug("[RAG] Raw LLM response (%d chars): %s", len(raw), raw[:500])

    # Strip <think>...</think> blocks from reasoning models (Qwen3, etc.)
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Also handle case where </think> is present but <think> was at the very start
    if cleaned.startswith("</think>"):
        cleaned = cleaned[len("</think>"):].strip()
    # Extract JSON from markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    log.debug("[RAG] Cleaned LLM output (%d chars): %s", len(cleaned), cleaned[:500])

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("[RAG] JSON parse failed: %s — cleaned text: %s", exc, cleaned[:300])
        return build_refusal_payload(language), meta

    if not isinstance(data, dict):
        log.error("[RAG] Parsed data is not a dict: %s", type(data))
        return build_refusal_payload(language), meta

    answer = str(data.get("answer", "")).strip()
    if answer in {REFUSAL_TEXT_EN, REFUSAL_TEXT_AR}:
        log.debug("[RAG] LLM returned refusal text")
        return build_refusal_payload(language), meta

    citations = data.get("citations")
    if not isinstance(citations, list) or not citations:
        log.error("[RAG] No citations in LLM response: %s", citations)
        return build_refusal_payload(language), meta

    log.debug("[RAG] LLM returned %d citations", len(citations))
    normalized_citations, used_rows = normalize_citations(citations, rows)
    if not normalized_citations:
        log.error("[RAG] Citation normalization failed. LLM citations: %s", json.dumps(citations[:2], default=str)[:500])
        log.error("[RAG] Available row keys: %s", [str(r.get('document_id'))[:8] + '/' + str(r.get('document_version')) + '/p' + str(r.get('page_start')) for r in rows[:3]])
        return build_refusal_payload(language), meta

    if not answer:
        log.error("[RAG] Empty answer after processing")
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
