from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "kib-knowledge-copilot-api"
    database_url: str = "postgresql://localhost/kib"
    rag_service_url: str = "http://localhost:8001"
    request_timeout_seconds: int = 90

    mock_oidc: bool = True
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_roles_claim: str = "roles"
    oidc_user_claim: str = "preferred_username"
    oidc_name_claim: str = "name"
    oidc_department_claim: str = "department"

    audit_read_roles: str = "compliance,audit_admin"

    model_config = {"env_prefix": "KIB_"}


settings = Settings()
