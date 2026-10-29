"""Typed request and response schemas for CMDB endpoints.

Field names, types and JSON shapes are part of the public API contract.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

CI_TYPE_LABELS: Dict[str, str] = {
    "application": "Application",
    "ai_model": "AI / ML model",
    "llm_app": "LLM / GenAI application",
    "data_pipeline": "Data pipeline",
    "platform": "Platform",
    "database": "Database",
    "infrastructure": "Infrastructure",
    "integration": "Integration / API",
    "other": "Other",
}


class ServiceResponse(BaseModel):
    """Matches ServiceSerializer fields exactly."""
    id: int
    name: str
    description: str = ""
    account: Optional[int] = None
    owner_team: Optional[int] = None
    criticality: int = 3
    is_active: bool = True


class PaginatedServiceResponse(BaseModel):
    """Matches DRF PageNumberPagination shape for services."""
    count: int
    next: Optional[str] = None
    previous: Optional[str] = None
    results: List[ServiceResponse]


class CIResponse(BaseModel):
    """Matches CISerializer fields exactly."""
    id: int
    name: str
    ci_type: str
    type_label: str
    environment: str
    tech_stack: str = ""
    version: str = ""
    account: Optional[int] = None
    service: Optional[int] = None
    owner_team: Optional[int] = None
    status: str = "active"
    attributes: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[int] = Field(default_factory=list)


class PaginatedCIResponse(BaseModel):
    """Matches DRF PageNumberPagination shape for CIs."""
    count: int
    next: Optional[str] = None
    previous: Optional[str] = None
    results: List[CIResponse]


class OpenTicketSummary(BaseModel):
    """Open tickets: id, number, subject."""
    id: int
    number: str
    subject: str


class CIImpactResponse(BaseModel):
    """Matches the CIViewSet.impact response shape, with explicit truncation status."""
    impacted: List[CIResponse]
    open_tickets: List[OpenTicketSummary]
    truncated: bool = False
