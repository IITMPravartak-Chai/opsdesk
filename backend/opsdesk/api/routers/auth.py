"""Authentication, profile and metadata router. Token contract is unchanged (HS256 access/refresh JWTs)."""
from __future__ import annotations

from typing import Optional

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import Response
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select, text
from sqlalchemy.orm import joinedload, selectinload
from starlette.concurrency import run_in_threadpool

from ...db.models import (Account, Category, ConfigurationItem, Priority, Service, State, Team, TeamMember, Tenant, TicketType,
                          User, Workflow)
from ...db.models.core import STAFF_ROLES
from ...db.models.workflow import IMPACT_LABELS, URGENCY_LABELS
from ...db.session import plain_session, tenant_session
from ...security import login_throttle, passwords, tokens
from ...services.audit import audit
from ...settings import settings
from ..runtime import run_in_tenant
from ..schemas.core import (AccountMeta, CategoryMeta, CIMeta, CustomFieldMeta, LabelValue, LoginRequest,
                            LoginResponse, MetaResponse, MfaSetupResponse, PriorityMeta, RefreshTokenRequest,
                            RefreshTokenResponse, ServiceMeta, TicketTypeMeta, UserProfileResponse)
from ..security import (AuthenticatedUser, _normalize_user_id, get_authenticated_user, get_current_user,
                        get_redis_client, load_current_role)

router = APIRouter(tags=["auth"])

BAD_LOGIN = "Email or password is incorrect."


def _active_user(s, user: AuthenticatedUser) -> User:
    """The caller's own active row in their own tenant. A deleted or deactivated account is refused even while its
    token is still unexpired."""
    u = s.scalars(select(User).options(joinedload(User.tenant), joinedload(User.account)).where(
        User.id == user.user_id, User.tenant_id == user.tenant_id, User.is_active.is_(True))).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or not found.")
    return u


def _user_payload(u: User) -> UserProfileResponse:
    return UserProfileResponse(
        id=u.id, email=u.email, name=u.display_name, first_name=u.first_name, role=u.role, is_agent=u.is_agent,
        tenant=u.tenant.name if u.tenant_id else None,
        account={"id": u.account_id, "name": u.account.name} if u.account_id else None,
        mfa_enabled=u.mfa_enabled, is_available=u.is_available,
    )


def authenticate(email: str, password: str):
    """Check credentials through the SECURITY DEFINER lookup (works before a tenant is known). Returns the lookup row
    or None; spends equivalent hashing time for unknown/inactive accounts (enumeration defence)."""
    if not email or not password:
        return None
    with plain_session() as s:
        row = s.execute(text(
            "SELECT user_id, email, password_hash, is_active, tenant_id, tenant_is_active, mfa_enabled, role "
            "FROM public.auth_lookup_user_by_email(:e)"), {"e": email.strip().lower()}).first()
    if row is None:
        passwords.burn_hash_time()
        return None
    ok = passwords.check_password(password, row.password_hash)
    if not row.is_active or not row.tenant_is_active or not row.tenant_id or not ok:
        return None
    if passwords.needs_rehash(row.password_hash):  # transparently upgrade old hashes at successful login
        with tenant_session(row.tenant_id) as s:
            u = s.get(User, row.user_id)
            if u is not None:
                u.password = passwords.make_password(password)
    return row


def _login(payload: LoginRequest, request: Request):
    login_clean = (payload.email or payload.username or "").strip().lower()
    throttle = login_throttle.LoginThrottle(
        get_redis_client(), settings.jwt_secret_key, login_clean,
        login_throttle.client_address(request.client.host if request.client else "", request.headers.get("x-forwarded-for")))
    try:
        throttle.reserve()
    except login_throttle.Throttled as t:
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={"detail": t.detail},
                            headers={"Retry-After": str(t.retry_after)})
    except login_throttle.StoreUnavailable:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": login_throttle.UNAVAILABLE})

    row = authenticate(login_clean, payload.password or "")
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=BAD_LOGIN)

    if row.mfa_enabled:
        # The body carries "mfa_required" like the reference LoginView; the sign-in form only shows its code field
        # when it sees that flag, so without it an MFA-enabled user could never finish signing in.
        code = str(payload.otp or "").strip()
        if not code:
            throttle.challenged()  # correct password; the code is the next step, not a failed attempt
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={
                "detail": "Enter the 6-digit code from your authenticator app.", "mfa_required": True})
        with tenant_session(row.tenant_id, commit=False) as s:
            secret = s.scalars(select(User.mfa_secret).where(User.id == row.user_id)).first()
        if not secret or not pyotp.TOTP(secret).verify(code, valid_window=1):
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={
                "detail": "That code didn't match. Try the newest code.", "mfa_required": True})

    throttle.succeeded()
    refresh, _claims = tokens.issue_refresh(row.user_id, row.tenant_id, row.role)
    with tenant_session(row.tenant_id) as s:
        u = _active_user(s, AuthenticatedUser(row.user_id, row.tenant_id, row.role))
        u.last_login = tokens_now()
        audit(s, row.tenant_id, "auth.login", u, u, request=request)
        body = _user_payload(u)
    return LoginResponse(access=tokens.issue_access_from(_claims), refresh=refresh, user=body)


def tokens_now():
    from ...db.base import utcnow
    return utcnow()


@router.post("/api/auth/login/", response_model=LoginResponse)
@router.post("/api/auth/token/", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request):
    """Authenticate with email and password, verifying TOTP MFA if enabled.

    Rate limited per account and per client address before any password is checked (login_throttle.py): 429 with
    Retry-After when a limit is reached, 503 if the shared limit store is unreachable (fail closed)."""
    return await run_in_threadpool(_login, payload, request)


@router.post("/api/auth/refresh/", response_model=RefreshTokenResponse)
@router.post("/api/auth/token/refresh/", response_model=RefreshTokenResponse)
async def refresh_token(payload: RefreshTokenRequest):
    """Rotate: mint a new access token and a new refresh token, and revoke the one presented."""
    def _do():
        try:
            claims = tokens.decode_refresh(payload.refresh)
        except tokens.TokenError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is invalid or expired")
        # The new access token copies the refresh token's claims, which date from login. Refuse a user who has
        # since been deactivated or deleted, and carry the role as it is now.
        user_id, tenant_id = _normalize_user_id(claims.get("user_id")), claims.get("tid")
        role = (load_current_role(user_id, tenant_id)
                if user_id and isinstance(tenant_id, int) and not isinstance(tenant_id, bool) and tenant_id > 0 else None)
        if not role:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or not found.")
        new_refresh, _ = tokens.issue_refresh(user_id, tenant_id, role)
        tokens.blacklist(claims)
        return RefreshTokenResponse(access=tokens.issue_access_from(claims, role=role), refresh=new_refresh)

    return await run_in_threadpool(_do)


@router.post("/api/auth/logout/", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshTokenRequest):
    """Revoke a refresh token. Always 204: an invalid or already revoked token needs no further action."""
    def _do():
        try:
            tokens.blacklist(tokens.decode_refresh(payload.refresh))
        except tokens.TokenError:
            pass

    await run_in_threadpool(_do)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/auth/me/", response_model=UserProfileResponse)
async def get_current_user_profile(user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Profile of the signed-in user, for every role. Read-only and limited to the caller's own row."""
    return await run_in_tenant(user, lambda s: _user_payload(_active_user(s, user)), commit=False)


@router.patch("/api/auth/me/", response_model=UserProfileResponse)
async def update_current_user_profile(payload: dict, user: AuthenticatedUser = Depends(get_current_user)):
    """Update profile fields (e.g. is_available for staff agents)."""
    def _do(s):
        u = s.scalars(select(User).options(joinedload(User.tenant), joinedload(User.account)).where(
            User.id == user.user_id, User.tenant_id == user.tenant_id)).first()
        if not u:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
        if "is_available" in payload and u.is_agent:
            u.is_available = bool(payload["is_available"])
        return _user_payload(u)

    return await run_in_tenant(user, _do)


@router.post("/api/auth/mfa/", response_model=MfaSetupResponse)
async def mfa_setup(payload: Optional[dict] = None, user: AuthenticatedUser = Depends(get_authenticated_user)):
    """MFA setup: provisioning URI on empty post, verification with code. Open to every signed-in user and acts only
    on the caller's own account (the token's user); no user id is accepted."""
    code = (payload or {}).get("code")

    def _do(s):
        u = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id)).first()
        if not u:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not configured for user.")
        if u.mfa_enabled:
            # Re-issuing the secret while MFA is on would let anyone holding a session token replace the account's
            # second factor without a current code. Refuse instead.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Two-step sign-in is already on.")
        if not code:
            u.mfa_secret = pyotp.random_base32()
            uri = pyotp.TOTP(u.mfa_secret).provisioning_uri(name=u.email, issuer_name="OpsDesk")
            return MfaSetupResponse(otpauth_uri=uri, secret=u.mfa_secret)
        if u.mfa_secret and pyotp.TOTP(u.mfa_secret).verify(str(code), valid_window=1):
            u.mfa_enabled = True
            audit(s, u.tenant_id, "auth.mfa_enabled", u, u)
            return MfaSetupResponse(mfa_enabled=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code didn't match.")

    return await run_in_tenant(user, _do)


@router.get("/api/meta/", response_model=MetaResponse)
async def get_meta(user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Everything the UI needs to render forms, scoped by the role stored on the user's row (not the token's claim):
    customers get only their own account's records; teams, staff and states stay staff-only."""
    def _do(s):
        tid = user.tenant_id
        u = _active_user(s, user)
        is_agent = u.is_agent

        types_stmt = (select(TicketType).options(selectinload(TicketType.custom_fields))
                      .where(TicketType.tenant_id == tid, TicketType.is_active.is_(True))
                      .order_by(TicketType.order, TicketType.name))
        if not is_agent:
            types_stmt = types_stmt.where(TicketType.customer_can_create.is_(True))
        types = s.scalars(types_stmt).all()

        accounts_stmt = select(Account).where(Account.tenant_id == tid, Account.is_active.is_(True)).order_by(Account.name)
        services_stmt = select(Service).where(Service.tenant_id == tid, Service.is_active.is_(True)).order_by(Service.name)
        cis_stmt = (select(ConfigurationItem).where(ConfigurationItem.tenant_id == tid,
                                                    ConfigurationItem.status != "retired").order_by(ConfigurationItem.name))
        if not is_agent:
            accounts = s.scalars(accounts_stmt.where(Account.id == u.account_id)).all()
            services = s.scalars(services_stmt.where(or_(Service.account_id == u.account_id,
                                                         Service.account_id.is_(None)))).all()
            cis = s.scalars(cis_stmt.where(ConfigurationItem.account_id == u.account_id)).all()
        else:
            accounts = s.scalars(accounts_stmt).all()
            services = s.scalars(services_stmt).all()
            cis = s.scalars(cis_stmt).all()

        ticket_types_meta = [
            TicketTypeMeta(
                id=t.id, key=t.key, name=t.name, prefix=t.prefix, workflow_id=t.workflow_id,
                custom_fields=[CustomFieldMeta(key=f.key, label=f.label, type=f.field_type, options=f.options,
                                               required=f.required, help_text=f.help_text)
                               for f in t.custom_fields if is_agent or f.visible_to_customer])
            for t in types]
        priorities_meta = [PriorityMeta(id=p.id, key=p.key, name=p.name, level=p.level, color=p.color)
                           for p in s.scalars(select(Priority).where(Priority.tenant_id == tid).order_by(Priority.level))]
        categories_meta = [CategoryMeta(id=c.id, name=str(c), ticket_type_id=c.ticket_type_id)
                           for c in s.scalars(select(Category).options(joinedload(Category.parent)).where(
                               Category.tenant_id == tid).order_by(Category.name)).unique()]
        accounts_meta = [AccountMeta(id=a.id, name=a.name, code=a.code, tier=a.tier) for a in accounts]
        services_meta = [ServiceMeta(id=x.id, name=x.name, account_id=x.account_id) for x in services]
        cis_meta = [CIMeta(id=c.id, name=c.name, type=c.ci_type, type_label=c.type_label, account_id=c.account_id,
                           environment=c.environment) for c in cis]

        teams_meta = agents_meta = states_meta = None
        if is_agent:
            members = {}
            for team_id, user_id in s.execute(select(TeamMember.team_id, TeamMember.user_id)
                                              .join(User, User.id == TeamMember.user_id)
                                              .where(User.tenant_id == tid, User.is_active.is_(True),
                                                     User.role.in_(STAFF_ROLES)).order_by(TeamMember.user_id)):
                members.setdefault(team_id, []).append(user_id)
            teams_meta = [{"id": t.id, "name": t.name, "member_ids": members.get(t.id, [])}
                          for t in s.scalars(select(Team).where(Team.tenant_id == tid).order_by(Team.name))]
            agents_meta = [{"id": a.id, "name": a.display_name, "is_available": a.is_available}
                           for a in s.scalars(select(User).where(User.tenant_id == tid, User.role.in_(STAFF_ROLES),
                                                                 User.is_active.is_(True)).order_by(User.id))]
            states_meta = [{"id": st.id, "key": st.key, "name": st.name, "category": st.category, "color": st.color,
                            "workflow_id": st.workflow_id}
                           for st in s.scalars(select(State).join(Workflow, Workflow.id == State.workflow_id).where(
                               Workflow.tenant_id == tid).order_by(State.order, State.id))]

        return MetaResponse(
            ticket_types=ticket_types_meta, priorities=priorities_meta, categories=categories_meta,
            accounts=accounts_meta, services=services_meta, cis=cis_meta,
            impact=[LabelValue(value=k, label=v) for k, v in IMPACT_LABELS.items()],
            urgency=[LabelValue(value=k, label=v) for k, v in URGENCY_LABELS.items()],
            teams=teams_meta, agents=agents_meta, states=states_meta)

    return await run_in_tenant(user, _do, commit=False)
