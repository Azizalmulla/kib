from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    id: Optional[UUID] = None
    email: str
    display_name: Optional[str] = None
    department: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)


class UserMeResponse(BaseModel):
    user: UserProfile
    roles: List[str]
    claims: Dict[str, Any]


class DocumentOut(BaseModel):
    id: UUID
    title: str
    doc_type: Optional[str] = None
    language: str
    status: str


class DocumentVersionOut(BaseModel):
    id: UUID
    version: str
    source_uri: str
    page_count: Optional[int] = None


class DocumentDetailResponse(BaseModel):
    document: DocumentOut
    active_version: Optional[DocumentVersionOut] = None


class Citation(BaseModel):
    doc_title: str
    doc_id: str
    document_version: str
    page_number: Optional[int] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    quote: str
    source_uri: str


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    language: Literal["en", "ar"] = Field(default="en")
    top_k: int = Field(default=5, ge=1, le=20)
    history: List[HistoryTurn] = Field(default_factory=list)


class ChatResponse(BaseModel):
    language: Literal["en", "ar"]
    answer: str
    confidence: Literal["high", "medium", "low"]
    citations: List[Citation]
    missing_info: Optional[str] = None
    safe_next_steps: List[str]


class AuditLogOut(BaseModel):
    id: UUID
    user_id: Optional[UUID]
    role_names: List[str]
    query: str
    retrieved_chunk_ids: List[UUID]
    answer: str
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    created_at: datetime
