import hashlib
from typing import List, Tuple

from sentence_transformers import SentenceTransformer

from .core.config import settings

_MODEL = None


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(settings.embedding_model)
    return _MODEL


def parse_document(filename: str, content: bytes) -> Tuple[str, dict]:
    lower = filename.lower()
    if lower.endswith(".txt"):
        text = content.decode("utf-8", errors="ignore")
        return text, {"page_count": 1}

    # TODO: add PDF/Office parsing + OCR pipeline.
    return "", {"page_count": None}


def chunk_text(text: str) -> List[dict]:
    chunks: List[dict] = []
    if not text:
        return chunks

    size = settings.chunk_size
    overlap = settings.chunk_overlap
    start = 0
    index = 0

    while start < len(text):
        end = min(len(text), start + size)
        chunk_text = text[start:end]
        chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        chunks.append(
            {
                "chunk_index": index,
                "text": chunk_text,
                "offset_start": start,
                "offset_end": end,
                "hash": chunk_hash,
                "page_start": 1,
                "page_end": 1,
            }
        )
        index += 1
        start = end - overlap if end - overlap > start else end

    return chunks


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = _get_model()
    passages = [f"passage: {text}" for text in texts]
    embeddings = model.encode(passages, normalize_embeddings=True)
    return [emb.tolist() for emb in embeddings]
