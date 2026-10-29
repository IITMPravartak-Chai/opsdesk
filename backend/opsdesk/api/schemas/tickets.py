"""Typed request and response schemas for Ticket endpoints.

Field names, types and JSON shapes are part of the public API contract.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TypeRef(BaseModel):
    key: str
    name: str


class StateRef(BaseModel):
    id: int
    key: str
    name: str
    category: str
    color: str


class PriorityRef(BaseModel):
    id: int
    key: str
    name: str
    level: int
    color: str


class AccountRef(BaseModel):
    id: int
    name: str
    code: Optional[str] = None


class UserRef(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None


class TeamRef(BaseModel):
    id: int
    name: str


class CategoryRef(BaseModel):
    id: int
    name: str


class ServiceRef(BaseModel):
    id: int
    name: str


class CIRef(BaseModel):
    id: int
    name: str
    type: str
    environment: str
    version: Optional[str] = None
    tech_stack: Optional[str] = None
    impacted: List[str] = Field(default_factory=list)


class TransitionRef(BaseModel):
    id: int
    name: str
    to: str
    to_category: str
    requires_comment: bool = False
    required_fields: List[str] = Field(default_factory=list)
    requires_approval: bool = False


class ApprovalRef(BaseModel):
    id: int
    approver: Dict[str, Any]
    status: str
    comment: Optional[str] = None
    decided_at: Optional[str] = None


class AttachmentRef(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int
    scan_status: str
    created_at: str
    interaction_id: Optional[int] = None
    url: str


class TicketLinkRef(BaseModel):
    type: str
    id: int
    number: str
    subject: str
    state: str


class RelatedTicketRef(BaseModel):
    id: int
    number: str
    subject: str
    state: Optional[str] = None


class TicketListItem(BaseModel):
    id: int
    number: str
    subject: str
    type: TypeRef
    state: StateRef
    priority: Optional[PriorityRef] = None
    account: Optional[AccountRef] = None
    requester: Optional[UserRef] = None
    assignee: Optional[UserRef] = None
    team: Optional[TeamRef] = None
    source: str
    sla_status: str
    response_due_at: Optional[str] = None
    resolution_due_at: Optional[str] = None
    first_responded_at: Optional[str] = None
    is_major_incident: bool = False
    tags: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    resolved_at: Optional[str] = None


class PaginatedTicketResponse(BaseModel):
    count: int
    next: Optional[str] = None
    previous: Optional[str] = None
    results: List[TicketListItem]


class TicketDetailResponse(TicketListItem):
    description: str = ""
    category: Optional[CategoryRef] = None
    service: Optional[ServiceRef] = None
    ci: Optional[CIRef] = None
    impact: Optional[int] = None
    urgency: Optional[int] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    resolution_code: Optional[str] = None
    resolution_notes: Optional[str] = None
    closed_at: Optional[str] = None
    response_breached: bool = False
    resolution_breached: bool = False
    sla_policy: Optional[str] = None
    transitions: List[TransitionRef] = Field(default_factory=list)
    approvals: List[ApprovalRef] = Field(default_factory=list)
    attachments: List[AttachmentRef] = Field(default_factory=list)
    links: List[TicketLinkRef] = Field(default_factory=list)
    parent: Optional[RelatedTicketRef] = None
    children: List[RelatedTicketRef] = Field(default_factory=list)
    time_logged_minutes: int = 0
    csat: Optional[Dict[str, Any]] = None
    ai: Optional[Dict[str, Any]] = None
    # Major incident (U6), as in the reference serializer; populated for staff only.
    incident_commander: Optional[UserRef] = None
    major_incident_status: Optional[str] = None
    post_incident_review: Optional[str] = None


MAJOR_STATUSES = ("investigating", "identified", "monitoring", "resolved")


class MajorDesignateRequest(BaseModel):
    """POST /api/tickets/{id}/designate_major/ (reference TicketViewSet.designate_major)."""
    is_major_incident: bool = True
    commander_id: Optional[int] = None
    status: Optional[str] = None


class MajorCoordinateRequest(BaseModel):
    """POST /api/tickets/{id}/major_coordinate/. Absent fields are left unchanged; an explicit
    null commander_id unassigns the commander."""
    status: Optional[str] = None
    commander_id: Optional[int] = None
    post_incident_review: Optional[str] = None
    link_ticket_ids: Optional[List[int]] = None
    note: Optional[str] = None


class TicketCountsResponse(BaseModel):
    mine: Optional[int] = None
    unassigned: Optional[int] = None
    sla: Optional[int] = None
    major: Optional[int] = None
    open: int


class TicketCreateRequest(BaseModel):
    ticket_type: int
    subject: str
    description: str = ""
    category: Optional[int] = None
    service: Optional[int] = None
    ci: Optional[int] = None
    impact: Optional[int] = None
    urgency: Optional[int] = None
    account: Optional[int] = None
    requester: Optional[int] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    is_major_incident: bool = False


class TicketUpdateRequest(BaseModel):
    subject: Optional[str] = None
    description: Optional[str] = None
    account: Optional[int] = None
    assignee: Optional[int] = None
    team: Optional[int] = None
    category: Optional[int] = None
    service: Optional[int] = None
    ci: Optional[int] = None
    impact: Optional[int] = None
    urgency: Optional[int] = None
    priority: Optional[int] = None
    tags: Optional[List[str]] = None
    custom_fields: Optional[Dict[str, Any]] = None
    is_major_incident: Optional[bool] = None
    parent: Optional[int] = None
    resolution_code: Optional[str] = None
    resolution_notes: Optional[str] = None


class TicketTransitionRequest(BaseModel):
    transition_id: int
    comment: Optional[str] = ""
    comment_public: Optional[bool] = None
    fields: Optional[Dict[str, Any]] = None


class AuthorRef(BaseModel):
    id: Optional[int] = None
    name: str
    is_agent: bool = False


class InteractionResponse(BaseModel):
    id: int
    kind: str
    direction: str
    body: str
    author: Optional[AuthorRef] = None
    source: str
    delivery_status: str
    delivery_error: Optional[str] = None
    created_at: str
    attachments: List[AttachmentRef] = Field(default_factory=list)
    attachment_errors: Optional[List[str]] = None


class EventResponse(BaseModel):
    id: int
    event_type: str
    actor: Optional[Dict[str, Any]] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CannedResponseItem(BaseModel):
    id: int
    title: str
    body: str
    shortcut: Optional[str] = None
    category: Optional[str] = None
    is_shared: bool = True


class CSATSurveyResponse(BaseModel):
    ticket: str
    subject: str
    score: Optional[int] = None
    responded: bool = False


class CSATSubmitRequest(BaseModel):
    score: int
    comment: Optional[str] = ""
