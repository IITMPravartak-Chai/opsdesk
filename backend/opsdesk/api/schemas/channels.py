"""Schemas for Channel management and webhooks."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ChannelResponse(BaseModel):
    id: int
    kind: str
    name: str
    is_active: bool = True
    settings: Dict[str, Any] = Field(default_factory=dict)
    inbound_url: str
    created_at: str
