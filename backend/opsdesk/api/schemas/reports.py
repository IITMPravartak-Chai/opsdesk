"""Schemas for reports and analytics endpoints."""
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class SeriesItem(BaseModel):
    date: str
    created: int
    resolved: int
    model_config = ConfigDict(extra="ignore")


class AgingItem(BaseModel):
    bucket: str
    count: int
    model_config = ConfigDict(extra="ignore")


class PriorityBreakdown(BaseModel):
    key: Optional[str] = None
    color: Optional[str] = None
    count: int
    model_config = ConfigDict(extra="ignore")


class StateBreakdown(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    count: int
    model_config = ConfigDict(extra="ignore")


class SourceBreakdown(BaseModel):
    source: Optional[str] = None
    count: int
    model_config = ConfigDict(extra="ignore")


class AccountBreakdown(BaseModel):
    name: Optional[str] = None
    count: int
    breached: int = 0
    model_config = ConfigDict(extra="ignore")


class TeamBreakdown(BaseModel):
    name: Optional[str] = None
    count: int
    model_config = ConfigDict(extra="ignore")


class OverviewReportResponse(BaseModel):
    period_days: int
    open: int
    unassigned: int
    at_risk: int
    breached_open: int
    major_incidents: int
    created: int
    resolved: int
    sla_compliance_pct: Optional[float] = None
    mttr_hours: Optional[float] = None
    first_response_hours: Optional[float] = None
    csat_avg: Optional[float] = None
    csat_responses: int
    ai_acceptance_pct: Optional[float] = None
    series: List[SeriesItem]
    aging: List[AgingItem]
    by_priority: List[PriorityBreakdown]
    by_state: List[StateBreakdown]
    by_source: List[SourceBreakdown]
    by_account: List[AccountBreakdown]
    by_team: List[TeamBreakdown]
    model_config = ConfigDict(extra="ignore")
