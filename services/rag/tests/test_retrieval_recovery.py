import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from services.rag.app.rag import merge_chunk_rows, expand_retrieval_question  # noqa: E402


def test_capital_adequacy_query_expands_retrieval_terms():
    expanded = expand_retrieval_question("ما هي تعليمات بنك الكويت المركزي بشأن كفاية رأس المال؟")

    assert "كفاية رأس المال" in expanded
    assert "Basel III" in expanded
    assert "Capital Adequacy Ratio" in expanded


def test_merge_chunk_rows_prefers_keyword_rows_before_vector_rows():
    keyword = [{"chunk_id": "same", "source": "keyword"}, {"chunk_id": "keyword-only"}]
    vector = [{"chunk_id": "same", "source": "vector"}, {"chunk_id": "vector-only"}]

    merged = merge_chunk_rows(keyword, vector)

    assert [row["chunk_id"] for row in merged] == ["same", "keyword-only", "vector-only"]
    assert merged[0]["source"] == "keyword"

