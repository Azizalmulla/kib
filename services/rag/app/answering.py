import json
import logging
import re
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from .guardrails import (
    REFUSAL_TEXT_AR,
    REFUSAL_TEXT_EN,
    build_answer_failed_payload,
    build_refusal_payload,
    compute_confidence,
    get_system_prompt,
    normalize_citations,
    safe_next_steps,
    translate_missing_info,
    validate_or_refuse,
)
from .core.config import settings
from .llm import LLMProvider


def _build_user_prompt(
    question: str,
    language: str,
    role_names: List[str],
    rows: List[Dict[str, Any]],
    history: List[Tuple[str, str]] = None,
    memory_summary: str = "",
) -> str:
    role_list = ", ".join(role_names) if role_names else "none"

    evidence_excerpts = []
    for idx, row in enumerate(rows, start=1):
        evidence_excerpts.append(
            "\n".join(
                [
                    f"Evidence excerpt {idx}:",
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

    evidence_block = "\n\n".join(evidence_excerpts)

    schema_example = '''{
  "answer": "Your polished answer here based only on the evidence excerpts.",
  "citations": [
    {
      "doc_id": "<exact doc_id from evidence excerpt>",
      "document_version": "<exact document_version from evidence excerpt>",
      "page_number": <exact page_number from evidence excerpt>,
      "start_offset": <exact start_offset from evidence excerpt>,
      "end_offset": <exact end_offset from evidence excerpt>,
      "source_uri": "<exact source_uri from evidence excerpt>",
      "quote": "<exact snippet from evidence text, max 25 words>"
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

    memory_block = ""
    if memory_summary:
        memory_block = "\n".join(
            [
                "",
                "User memory (for context only; do not use as a source of truth):",
                memory_summary[:1200],
                "",
            ]
        )

    return "\n".join(
        [
            "You MUST answer using ONLY the evidence excerpts below.",
            "Conversation history and user memory may clarify intent, but they are NOT evidence.",
            "If the evidence is insufficient, return the refusal message exactly.",
            "Return ONLY valid JSON matching the EXACT schema below. No other fields allowed.",
            "Use the same language as the user for the answer.",
            "Write the answer as a polished KIB copilot reply, not as a retrieval/debug summary.",
            "Start with the answer, then add concise supporting details if useful.",
            "Use bullets when the evidence contains multiple points, laws, requirements, or steps.",
            "Never use the words chunks, retrieved context, or phrases that expose retrieval internals.",
            "Avoid weak filler like 'These sources reference...'. Prefer 'KIB sources point to...' or 'The evidence I found says...'.",
            "If evidence mentions a law/regulation but not the full legal text, say that clearly and offer to search for a more specific clause.",
            "Each citation must use the EXACT values from the evidence metadata (doc_id, document_version, page_number, start_offset, end_offset, source_uri).",
            "The quote must be an exact snippet from the evidence text, max 25 words, NOT translated.",
            "",
            "REQUIRED JSON SCHEMA:",
            schema_example,
            "",
            f"User language: {language}",
            f"User roles: {role_list}",
            memory_block,
            history_block,
            f"User question: {question}",
            "",
            "Evidence excerpts:",
            evidence_block,
        ]
    )


def answer_with_llm(
    rows: List[Dict[str, Any]],
    question: str,
    language: str,
    role_names: List[str],
    provider: LLMProvider,
    history: List[Tuple[str, str]] = None,
    memory_summary: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    language = "ar" if language == "ar" else "en"
    meta = {
        "retrieved_chunk_ids": [str(row["chunk_id"]) for row in rows],
    }

    if not rows:
        return build_refusal_payload(language), meta

    prompt = _build_user_prompt(
        question,
        language,
        role_names,
        rows,
        history=history,
        memory_summary=memory_summary,
    )
    system_prompt = get_system_prompt(role_names)
    log.debug("[RAG] Sending prompt to LLM (%d chars, %d chunks, roles=%s)", len(prompt), len(rows), role_names)

    raw = ""
    last_exc: Exception | None = None
    for attempt in range(1, max(1, settings.llm_max_attempts) + 1):
        try:
            raw = provider.generate(system_prompt, prompt)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            log.warning("[RAG] LLM call attempt %s failed: %s", attempt, exc)
    if last_exc is not None:
        log.error("[RAG] LLM call failed after retries: %s", last_exc)
        meta["llm_error"] = str(last_exc)
        return build_answer_failed_payload(language), meta

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

    if not cleaned:
        log.error("[RAG] LLM returned empty content")
        meta["llm_error"] = "empty_response"
        return build_answer_failed_payload(language), meta

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("[RAG] JSON parse failed: %s — cleaned text: %s", exc, cleaned[:300])
        meta["llm_error"] = "json_parse_failed"
        return build_answer_failed_payload(language), meta

    if not isinstance(data, dict):
        log.error("[RAG] Parsed data is not a dict: %s", type(data))
        meta["llm_error"] = "non_dict_response"
        return build_answer_failed_payload(language), meta

    answer = str(data.get("answer", "")).strip()
    if answer in {REFUSAL_TEXT_EN, REFUSAL_TEXT_AR}:
        log.debug("[RAG] LLM returned refusal text")
        return build_refusal_payload(language), meta

    if not answer:
        log.error("[RAG] Empty answer after processing")
        meta["llm_error"] = "empty_answer"
        return build_answer_failed_payload(language), meta

    citations = data.get("citations") if isinstance(data.get("citations"), list) else []
    log.debug("[RAG] LLM returned %d citations", len(citations))
    normalized_citations, used_rows = normalize_citations(citations, rows)

    if not normalized_citations:
        log.warning(
            "[RAG] Citation normalization failed; falling back to top retrieved row. "
            "LLM citations=%s rows=%s",
            json.dumps(citations[:2], default=str)[:300],
            [
                str(r.get("document_id"))[:8] + "/" + str(r.get("document_version")) + "/p" + str(r.get("page_start"))
                for r in rows[:3]
            ],
        )
        fallback_row = rows[0]
        used_rows = [fallback_row]
        normalized_citations = [
            {
                "doc_title": fallback_row.get("document_title"),
                "doc_id": str(fallback_row.get("document_id")),
                "document_version": fallback_row.get("document_version"),
                "page_number": fallback_row.get("page_start"),
                "start_offset": fallback_row.get("offset_start"),
                "end_offset": fallback_row.get("offset_end"),
                "quote": (str(fallback_row.get("text") or "").strip().split("\n", 1)[0])[:200],
                "source_uri": fallback_row.get("source_uri"),
            }
        ]
        meta["citation_fallback"] = True

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
