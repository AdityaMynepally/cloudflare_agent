"""Pydantic models for the chat API."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field
import uuid


class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    role: str  # "user" or "assistant"
    content: str
    message_type: str = "text"  # text, audit_progress, page_result, error
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class AuditProgress(BaseModel):
    status: str  # discovering, auditing, summarizing, complete, error
    message: str
    current_page: Optional[int] = None
    total_pages: Optional[int] = None
    page_url: Optional[str] = None


class SSEEvent(BaseModel):
    event: str  # progress, page_result, complete, error, message
    data: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    response: str
    audit_id: Optional[str] = None
