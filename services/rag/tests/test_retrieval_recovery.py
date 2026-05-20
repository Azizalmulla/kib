import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from services.rag.app.rag import expand_retrieval_question  # noqa: E402


def test_capital_adequacy_query_expands_retrieval_terms():
    expanded = expand_retrieval_question("ما هي تعليمات بنك الكويت المركزي بشأن كفاية رأس المال؟")

    assert "كفاية رأس المال" in expanded
    assert "Basel III" in expanded
    assert "Capital Adequacy Ratio" in expanded

