from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "kib-knowledge-copilot-rag"
    database_url: str = "postgresql://localhost/kib"

    default_top_k: int = 5
    vector_probes: int = 10

    fireworks_api_key: str = ""
    fireworks_embed_url: str = "https://api.fireworks.ai/inference/v1/embeddings"
    embedding_dim: int = 768
    embedding_model: str = "accounts/fireworks/models/qwen3-embedding-8b"

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.fireworks.ai/inference"
    llm_model: str = "accounts/fireworks/models/qwen3-8b"
    llm_api_key: str = ""
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 700
    llm_reasoning_effort: str = "low"
    llm_response_format: str = "json_object"

    model_config = {"env_prefix": "KIB_"}


settings = Settings()
