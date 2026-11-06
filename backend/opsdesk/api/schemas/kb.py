"""Schemas for Knowledge Base Articles."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ArticleResponse(BaseModel):
    id: int
    title: str
    body: str
    category: Optional[int] = None
    account: Optional[int] = None
    visibility: str = "public"
    status: str = "published"
    tags: List[str] = Field(default_factory=list)
    views: int = 0
    helpful_votes: int = 0
    unhelpful_votes: int = 0
    author: Optional[str] = None
    source_ticket: Optional[int] = None
    created_at: str
    updated_at: str
    duplicate: Optional[bool] = None
    generation_method: Optional[str] = None


class ArticleCreateRequest(BaseModel):
    title: str
    body: str
    category: Optional[int] = None
    account: Optional[int] = None
    visibility: str = "public"
    status: str = "published"
    tags: List[str] = Field(default_factory=list)


class ArticleUpdateRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[int] = None
    account: Optional[int] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None


class ArticleVoteRequest(BaseModel):
    helpful: bool = True


class ArticleFromTicketRequest(BaseModel):
    ticket_id: int
