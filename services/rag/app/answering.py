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


ANSWER_STOPWORDS = {
    "about",
    "also",
    "and",
    "are",
    "can",
    "does",
    "for",
    "from",
    "has",
    "how",
    "into",
    "kib",
    "kuwait",
    "please",
    "some",
    "that",
    "the",
    "their",
    "there",
    "this",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "قانون",
    "ما",
    "ماهي",
    "ماهي",
    "هي",
    "عن",
    "في",
    "من",
}


def _answer_terms(text: str) -> List[str]:
    terms: List[str] = []
    for token in re.findall(r"[\w\u0600-\u06ff]+", text.lower()):
        if len(token) < 3 or token in ANSWER_STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _sentence_candidates(text: str) -> List[str]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    sentences = re.split(r"(?<=[.!؟?])\s+|\n+", cleaned)
    return [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 20]


def _row_similarity(row: Dict[str, Any]) -> float:
    distance = row.get("distance")
    if distance is None:
        return 0.0
    try:
        return max(0.0, min(1.0, 1.0 - float(distance)))
    except (TypeError, ValueError):
        return 0.0


def _row_rerank_score(row: Dict[str, Any]) -> float | None:
    score = row.get("rerank_score")
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def _row_evidence_score(row: Dict[str, Any], terms: List[str]) -> tuple[int, float, List[str]]:
    haystack = " ".join(
        [
            str(row.get("document_title") or ""),
            str(row.get("section") or ""),
            str(row.get("text") or ""),
            str(row.get("source_uri") or ""),
        ]
    ).lower()
    matched = [term for term in terms if term in haystack]
    return len(matched), _row_similarity(row), matched


def _is_strong_evidence(row: Dict[str, Any], terms: List[str]) -> bool:
    if not terms:
        return False
    matched_count, similarity, _ = _row_evidence_score(row, terms)
    required_matches = 2 if len(terms) >= 2 else 1
    return matched_count >= required_matches and similarity >= 0.35


def _strong_rows_from_reranker(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scored_rows = [(row, _row_rerank_score(row)) for row in rows]
    scored_rows = [(row, score) for row, score in scored_rows if score is not None]
    if not scored_rows:
        return []

    scored_rows.sort(key=lambda item: item[1], reverse=True)
    top_row, top_score = scored_rows[0]
    second_score = scored_rows[1][1] if len(scored_rows) > 1 else 0.0
    score_gap = top_score - second_score

    if top_score >= settings.evidence_rerank_score_threshold:
        return [row for row, score in scored_rows[:2] if score >= settings.evidence_rerank_min_score]

    if top_score >= settings.evidence_rerank_min_score and score_gap >= settings.evidence_rerank_gap_threshold:
        return [top_row]

    if len(scored_rows) > 1 and top_score >= settings.evidence_rerank_min_score:
        top_doc_id = str(top_row.get("document_id"))
        same_doc_rows = [
            row
            for row, score in scored_rows[:3]
            if str(row.get("document_id")) == top_doc_id and score >= settings.evidence_rerank_min_score
        ]
        if len(same_doc_rows) >= 2:
            return same_doc_rows[:2]

    return []


def _best_evidence_sentences(row: Dict[str, Any], terms: List[str], limit: int = 2) -> List[str]:
    scored: List[tuple[int, int, str]] = []
    for index, sentence in enumerate(_sentence_candidates(str(row.get("text") or ""))):
        lowered = sentence.lower()
        score = sum(1 for term in terms if term in lowered)
        if re.search(r"\d+(?:\.\d+)?\s*%|\b\d{4}\b|law no\.?|article", lowered):
            score += 1
        if score:
            scored.append((score, index, sentence))

    if not scored:
        fallback = " ".join(str(row.get("text") or "").split())
        return [fallback[:500]] if fallback else []

    selected = sorted(sorted(scored, key=lambda item: item[0], reverse=True)[:limit], key=lambda item: item[1])
    return [sentence for _, _, sentence in selected]


def _citation_from_row(row: Dict[str, Any], quote_text: str) -> Dict[str, Any]:
    return {
        "doc_title": row.get("document_title"),
        "doc_id": str(row.get("document_id")),
        "document_version": row.get("document_version"),
        "page_number": row.get("page_start"),
        "start_offset": row.get("offset_start"),
        "end_offset": row.get("offset_end"),
        "quote": " ".join(quote_text.split()[:25]),
        "source_uri": row.get("source_uri"),
    }


def _extractive_fallback_payload(
    rows: List[Dict[str, Any]],
    question: str,
    language: str,
) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]]]:
    terms = _answer_terms(question)
    strong_rows = _strong_rows_from_reranker(rows) or [row for row in rows if _is_strong_evidence(row, terms)]
    if not strong_rows:
        return None, []

    strong_rows.sort(
        key=lambda row: (
            _row_rerank_score(row) if _row_rerank_score(row) is not None else -1.0,
            _row_evidence_score(row, terms)[0],
            _row_similarity(row),
        ),
        reverse=True,
    )
    used_rows = strong_rows[:2]
    sentences: List[str] = []
    citations: List[Dict[str, Any]] = []
    for row in used_rows:
        row_sentences = _best_evidence_sentences(row, terms, limit=2)
        if not row_sentences:
            continue
        sentences.extend(row_sentences)
        citations.append(_citation_from_row(row, row_sentences[0]))

    if not sentences or not citations:
        return None, []

    body = " ".join(sentences)
    if language == "ar":
        answer = f"وفقاً للأدلة المتاحة من مستندات KIB المعتمدة: {body}"
    else:
        answer = body

    confidence = compute_confidence(used_rows, citations)
    if confidence == "low":
        confidence = "medium"

    return {
        "language": language,
        "answer": answer,
        "confidence": confidence,
        "citations": citations,
        "missing_info": None,
        "safe_next_steps": safe_next_steps(language),
    }, used_rows


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


def _build_polish_prompt(
    question: str,
    language: str,
    role_names: List[str],
    rows: List[Dict[str, Any]],
    draft_payload: Dict[str, Any],
) -> str:
    role_list = ", ".join(role_names) if role_names else "none"
    draft_citations = draft_payload.get("citations") or []
    evidence_excerpts = []
    for idx, row in enumerate(rows, start=1):
        evidence_excerpts.append(
            "\n".join(
                [
                    f"Evidence excerpt {idx}:",
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

    schema_example = '''{
  "answer": "Polished version of the grounded draft. Do not add facts.",
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

    return "\n".join(
        [
            "Polish the grounded draft answer below for the user.",
            "The draft answer is already grounded in approved KIB evidence and is the source of truth.",
            "Do NOT decide whether to refuse. Do NOT return a refusal. Do NOT add facts, numbers, dates, policies, fees, or limits not present in the draft/evidence.",
            "Preserve all material facts from the draft. Use concise professional wording.",
            "Return ONLY valid JSON matching the schema. No other fields allowed.",
            "",
            "REQUIRED JSON SCHEMA:",
            schema_example,
            "",
            f"User language: {language}",
            f"User roles: {role_list}",
            f"User question: {question}",
            "",
            "Grounded draft answer:",
            str(draft_payload.get("answer") or ""),
            "",
            "Grounded draft citations:",
            json.dumps(draft_citations, ensure_ascii=False),
            "",
            "Evidence excerpts:",
            "\n\n".join(evidence_excerpts),
        ]
    )


def _polish_system_prompt() -> str:
    return (
        "You are the KIB Knowledge Copilot. Your task is only to polish a grounded draft answer. "
        "The backend has already selected approved evidence and decided the question is answerable. "
        "Do not refuse, do not add facts, and do not use outside knowledge. Return JSON only."
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

    extractive_payload, extractive_rows = _extractive_fallback_payload(rows, question, language)
    if extractive_payload is not None:
        meta["extractive_main"] = True
        meta["extractive_chunk_ids"] = [str(row.get("chunk_id")) for row in extractive_rows]

    def maybe_extractive_fallback(reason: str) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
        if extractive_payload is None:
            return None, meta
        meta["extractive_fallback"] = True
        meta["extractive_fallback_reason"] = reason
        meta["extractive_fallback_chunk_ids"] = [str(row.get("chunk_id")) for row in extractive_rows]
        return extractive_payload, meta

    if extractive_payload is not None:
        prompt = _build_polish_prompt(question, language, role_names, rows, extractive_payload)
        system_prompt = _polish_system_prompt()
    else:
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
        fallback_payload, fallback_meta = maybe_extractive_fallback("llm_exception")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
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
        fallback_payload, fallback_meta = maybe_extractive_fallback("empty_response")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
        return build_answer_failed_payload(language), meta

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("[RAG] JSON parse failed: %s — cleaned text: %s", exc, cleaned[:300])
        meta["llm_error"] = "json_parse_failed"
        fallback_payload, fallback_meta = maybe_extractive_fallback("json_parse_failed")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
        return build_answer_failed_payload(language), meta

    if not isinstance(data, dict):
        log.error("[RAG] Parsed data is not a dict: %s", type(data))
        meta["llm_error"] = "non_dict_response"
        fallback_payload, fallback_meta = maybe_extractive_fallback("non_dict_response")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
        return build_answer_failed_payload(language), meta

    answer = str(data.get("answer", "")).strip()
    if answer in {REFUSAL_TEXT_EN, REFUSAL_TEXT_AR}:
        log.debug("[RAG] LLM returned refusal text")
        fallback_payload, fallback_meta = maybe_extractive_fallback("llm_refusal")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
        return build_refusal_payload(language), meta

    if not answer:
        log.error("[RAG] Empty answer after processing")
        meta["llm_error"] = "empty_answer"
        fallback_payload, fallback_meta = maybe_extractive_fallback("empty_answer")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
        return build_answer_failed_payload(language), meta

    citations = data.get("citations") if isinstance(data.get("citations"), list) else []
    log.debug("[RAG] LLM returned %d citations", len(citations))
    normalized_citations, used_rows = normalize_citations(citations, rows)

    if not normalized_citations:
        fallback_payload, fallback_meta = maybe_extractive_fallback("citation_normalization_failed")
        if fallback_payload is not None:
            return fallback_payload, fallback_meta
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
