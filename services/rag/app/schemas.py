from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    id: str
    role_names: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)


class RagRequest(BaseModel):
    question: str
    language: Literal["en", "ar"] = "en"
    top_k: int = 5
    user: UserContext


class Citation(BaseModel):
    doc_title: str
    doc_id: str
    document_version: str
    page_number: Optional[int] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    quote: str
    source_uri: str

    class Config:
        extra = "forbid"


class StrictRagResponse(BaseModel):
    language: Literal["en", "ar"]
    answer: str
    confidence: Literal["high", "medium", "low"]
    citations: List[Citation]
    missing_info: Optional[str] = None
    safe_next_steps: List[str]

    class Config:
        extra = "forbid"


class ModelInfo(BaseModel):
    provider: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
