"""Schemas for Authentication, User Profile, and Metadata endpoints."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    password: str
    otp: Optional[str] = None


class UserProfileResponse(BaseModel):
    id: int
    email: str
    name: str
    first_name: Optional[str] = None
    role: str
    is_agent: bool
    tenant: Optional[str] = None
    account: Optional[Dict[str, Any]] = None
    mfa_enabled: bool = False
    is_available: bool = True


class LoginResponse(BaseModel):
    access: str
    refresh: str
    user: UserProfileResponse


class RefreshTokenRequest(BaseModel):
    refresh: str


class RefreshTokenResponse(BaseModel):
    access: str
    refresh: Optional[str] = None


class MfaSetupResponse(BaseModel):
    otpauth_uri: Optional[str] = None
    secret: Optional[str] = None
    mfa_enabled: Optional[bool] = None


class CustomFieldMeta(BaseModel):
    key: str
    label: str
    type: str
    options: Optional[List[str]] = None
    required: bool = False
    help_text: Optional[str] = None


class TicketTypeMeta(BaseModel):
    id: int
    key: str
    name: str
    prefix: str
    workflow_id: int
    custom_fields: List[CustomFieldMeta] = Field(default_factory=list)


class PriorityMeta(BaseModel):
    id: int
    key: str
    name: str
    level: int
    color: str


class CategoryMeta(BaseModel):
    id: int
    name: str
    ticket_type_id: Optional[int] = None


class AccountMeta(BaseModel):
    id: int
    name: str
    code: Optional[str] = None
    tier: Optional[str] = None


class ServiceMeta(BaseModel):
    id: int
    name: str
    account_id: Optional[int] = None


class CIMeta(BaseModel):
    id: int
    name: str
    type: str
    type_label: str
    account_id: Optional[int] = None
    environment: str


class LabelValue(BaseModel):
    value: int
    label: str


class MetaResponse(BaseModel):
    ticket_types: List[TicketTypeMeta]
    priorities: List[PriorityMeta]
    categories: List[CategoryMeta]
    accounts: List[AccountMeta]
    services: List[ServiceMeta]
    cis: List[CIMeta]
    impact: List[LabelValue]
    urgency: List[LabelValue]
    teams: Optional[List[Dict[str, Any]]] = None
    agents: Optional[List[Dict[str, Any]]] = None
    states: Optional[List[Dict[str, Any]]] = None
