from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "kib-knowledge-copilot-rag"
    database_url: str = "postgresql://localhost/kib"

    default_top_k: int = 5
    vector_probes: int = 10
    hnsw_ef_search: int = 100
    rerank_enabled: bool = True
    rerank_candidate_k: int = 80
    recovery_candidate_k: int = 120
    keyword_candidate_k: int = 30
    rerank_top_n: int = 4

    fireworks_api_key: str = ""
    fireworks_embed_url: str = "https://api.fireworks.ai/inference/v1/embeddings"
    fireworks_rerank_url: str = "https://api.fireworks.ai/inference/v1/rerank"
    embedding_dim: int = 768
    embedding_model: str = "accounts/fireworks/models/qwen3-embedding-8b"
    reranker_model: str = "accounts/fireworks/models/qwen3-reranker-8b"

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.fireworks.ai/inference"
    llm_model: str = "accounts/fireworks/models/qwen3p6-plus"
    llm_api_key: str = ""
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 700
    llm_reasoning_effort: str = "none"
    llm_response_format: str = "json_object"

    model_config = {"env_prefix": "KIB_"}


settings = Settings()
