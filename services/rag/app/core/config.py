from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "kib-knowledge-copilot-rag"
    database_url: str = "postgresql://localhost/kib"

    default_top_k: int = 5

    fireworks_api_key: str = "fw_JQzU8TxGETYnmNxpMDDyAE"
    fireworks_embed_url: str = "https://api.fireworks.ai/inference/v1/embeddings"
    embedding_dim: int = 768
    embedding_model: str = "accounts/fireworks/models/qwen3-embedding-8b"

    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.fireworks.ai/inference"
    llm_model: str = "accounts/fireworks/models/qwen3-8b"
    llm_api_key: str = "fw_JQzU8TxGETYnmNxpMDDyAE"
    llm_timeout_seconds: int = 60

    model_config = {"env_prefix": "KIB_"}


settings = Settings()
