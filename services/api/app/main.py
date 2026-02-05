from fastapi import FastAPI

from .core.config import settings
from .routers import audit, auth, chat, documents

app = FastAPI(title=settings.app_name)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(audit.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
