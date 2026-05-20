import os
import subprocess

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .routers import admin, audit, auth, chat, documents, feedback


def _resolve_build_marker() -> str:
    env_marker = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or os.getenv("BUILD_COMMIT")
    if env_marker:
        return env_marker[:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


BUILD_MARKER = _resolve_build_marker()

app = FastAPI(title=settings.app_name)

cors_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(audit.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "build": BUILD_MARKER}
