from pydantic import BaseSettings


class Settings(BaseSettings):
    app_name: str = "kib-knowledge-copilot-ingestion"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/kib"
    uploads_dir: str = "/Users/azizalmulla/Desktop/kib/services/ingestion/data/uploads"

    chunk_size: int = 800
    chunk_overlap: int = 100

    embedding_dim: int = 768
    embedding_model: str = "intfloat/multilingual-e5-base"

    class Config:
        env_prefix = "KIB_"


settings = Settings()
