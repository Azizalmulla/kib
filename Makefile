.PHONY: test-rag ci

test-rag:
	python -m pytest services/rag/tests

ci: test-rag
