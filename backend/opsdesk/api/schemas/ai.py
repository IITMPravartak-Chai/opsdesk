"""Schemas for AI Deflection, Suggestions, and Feedback."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DeflectionSearchRequest(BaseModel):
    text: str


class DeflectionSearchResponse(BaseModel):
    tickets: List[Dict[str, Any]] = Field(default_factory=list)
    articles: List[Dict[str, Any]] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    ticket_id: Optional[int] = None
    feature: str = "similar"
    suggestion: Dict[str, Any] = Field(default_factory=dict)
    accepted: bool = False
