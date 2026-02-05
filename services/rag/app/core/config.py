from pydantic import BaseSettings


class Settings(BaseSettings):
    app_name: str = "kib-knowledge-copilot-rag"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/kib"

    default_top_k: int = 5

    embedding_dim: int = 768
    embedding_model: str = "intfloat/multilingual-e5-base"

    llm_provider: str = "mock"
    llm_base_url: str = "http://localhost:8003"
    llm_model: str = "llama3"
    llm_api_key: str = ""
    llm_timeout_seconds: int = 30

    class Config:
        env_prefix = "KIB_"


settings = Settings()
