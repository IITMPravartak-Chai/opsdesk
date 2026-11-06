"""Channels: read access for managers, the public inbound webhook receiver and the IMAP OAuth2 flow."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from ...channels.base import InboundRequest, UploadedFile
from ...channels.oauth import (OAuthStateError, exchange_code_for_tokens, get_authorization_url,
                               validate_oauth_state)
from ...db.models import Channel, User
from ...db.models.core import ADMIN_ROLES
from ...db.session import plain_session, tenant_session
from ...settings import settings
from ...tenancy import _local_store
from ...worker.tasks import accept
from ..runtime import run_in_tenant
from ..schemas.channels import ChannelResponse
from ..security import AuthenticatedUser, get_current_user, get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

MAX_INBOUND_BYTES = 1024 * 1024


def channel_response(c: Channel) -> ChannelResponse:
    return ChannelResponse(
        id=c.id, kind=c.kind, name=c.name, is_active=c.is_active, settings=c.settings or {},
        inbound_url=f"{settings.public_base_url}/api/channels/inbound/{c.inbound_token}/",
        created_at=c.created_at.isoformat() if c.created_at else "")


def _require_manager(user: AuthenticatedUser):
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Channels are managed in Administration, which only the super admin can open.")


@router.get("/", response_model=List[ChannelResponse])
async def list_channels(user: AuthenticatedUser = Depends(get_current_user)):
    """List configured communication channels (managers only)."""
    _require_manager(user)
    return await run_in_tenant(user, lambda s: [
        channel_response(c) for c in s.scalars(select(Channel).where(Channel.tenant_id == user.tenant_id)
                                               .order_by(Channel.id))], commit=False)


@router.get("/oauth-callback/")
async def oauth_callback(request: Request, state: str = Query(None), code: str = Query(None), error: str = Query(None)):
    """OAuth2 callback: validates the signed state, exchanges the code for tokens and stores them encrypted.
    A browser (Accept: text/html) is sent back into the app; API clients get JSON."""
    def _do():
        if error:
            logger.warning("OAuth provider returned error: %s", error)
            raise HTTPException(status_code=400, detail=f"Provider authorization error: {error}")
        if not state or not code:
            raise HTTPException(status_code=400, detail="Missing state or code parameter.")
        try:
            data = validate_oauth_state(state)
        except OAuthStateError as exc:
            logger.warning("Invalid OAuth state: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid or expired OAuth state token.")

        channel_id, tenant_id, user_id = data["channel_id"], data["tenant_id"], data.get("user_id")
        key = f"opsdesk:oauth_code:{hashlib.sha256(code.encode('utf-8')).hexdigest()}"
        if settings.use_local_cache:
            fresh = _local_store.set_nx(key, 600)
        else:
            fresh = bool(get_redis_client().set(key, "1", nx=True, ex=600))
        if not fresh:
            raise HTTPException(status_code=400, detail="Authorization code has already been used.")

        with tenant_session(tenant_id) as s:
            initiator = s.scalars(select(User).where(User.id == user_id, User.tenant_id == tenant_id,
                                                     User.is_active.is_(True))).first()
            if not initiator or initiator.role not in ADMIN_ROLES:
                raise HTTPException(status_code=403, detail="Initiating user is no longer authorized.")
            channel = s.get(Channel, channel_id)
            if not channel:
                raise HTTPException(status_code=404, detail="Channel not found.")
            redirect_uri = f"{settings.public_base_url}/api/channels/oauth-callback/"
            provider = (channel.config or {}).get("provider", "microsoft")
            try:
                res = exchange_code_for_tokens(s, channel, code, redirect_uri=redirect_uri, provider=provider)
            except Exception as exc:  # noqa: BLE001
                logger.error("Token exchange failed for channel %s: %s", channel_id, type(exc).__name__)
                raise HTTPException(status_code=502, detail="Failed to exchange authorization code for tokens.")
            return {"status": "authorized", "channel_id": channel_id, "oauth_status": res["status"]}

    wants_html = "text/html" in request.headers.get("accept", "")
    try:
        result = await run_in_threadpool(_do)
    except HTTPException:
        if wants_html:
            return RedirectResponse(f"{settings.public_base_url}/settings/config/channels?oauth=error", status_code=303)
        raise
    if wants_html:
        return RedirectResponse(f"{settings.public_base_url}/settings/config/channels?oauth=authorized", status_code=303)
    return result


@router.api_route("/inbound/{token}/", methods=["GET", "POST"])
async def inbound_webhook(request: Request, token: str = Path(..., min_length=16, max_length=128)):
    """Public webhook receiver for incoming channel events."""
    try:
        def _resolve():
            with plain_session() as s:
                return s.execute(text("SELECT channel_id, tenant_id, kind, is_active "
                                      "FROM public.resolve_channel_by_inbound_token(:t)"), {"t": token}).first()
        row = await run_in_threadpool(_resolve)
    except Exception as exc:  # noqa: BLE001
        logger.error("Channel token resolution database failure: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Channel service temporarily unavailable.")
    if not row or not row.channel_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")

    # The channel's adapter answers provider handshakes, then must verify the sender (HMAC / bearer / token, per
    # channel) before its own parser turns the body into envelopes.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_INBOUND_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload exceeds 1MB limit.")
    raw_body = await request.body()
    if len(raw_body) > MAX_INBOUND_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload exceeds 1MB limit.")

    form: dict = {}
    files: list = []
    ctype = request.headers.get("content-type", "")
    if ctype.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        parsed = await request.form()
        for key, value in parsed.multi_items():
            if hasattr(value, "filename"):
                files.append(UploadedFile(name=value.filename or "file", content_type=value.content_type or "",
                                          data=await value.read()))
            else:
                form[key] = value
    inbound = InboundRequest(method=request.method, body=raw_body, headers=dict(request.headers),
                             query=dict(request.query_params), form=form, files=files)

    def _process():
        with tenant_session(row.tenant_id) as s:
            channel = s.get(Channel, row.channel_id)
            if not channel:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")
            adapter = channel.adapter
            hs = adapter.handshake(inbound)
            if hs is not None:
                return Response(content=hs.body, status_code=hs.status, media_type=hs.content_type)
            if request.method != "POST":
                raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Method not allowed.")
            if not adapter.verify(inbound):
                logger.warning("inbound signature rejected channel=%s", channel.id)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature rejected.")
            try:
                result = adapter.parse(inbound)
            except (ValueError, json.JSONDecodeError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload.")
            return {"accepted": accept(s, channel, result)}

    return await run_in_threadpool(_process)


@router.get("/{id}/oauth-initiate/")
async def oauth_initiate(id: int = Path(..., ge=1), provider: str = Query("microsoft"),
                         user: AuthenticatedUser = Depends(get_current_user)):
    """Generate the provider authorization URL and signed state token. Never returns client secrets or tokens."""
    _require_manager(user)

    def _do(s):
        channel = s.scalars(select(Channel).where(Channel.id == id, Channel.tenant_id == user.tenant_id)).first()
        if not channel:
            raise HTTPException(status_code=404, detail="Not found.")
        if channel.kind != "imap":
            raise HTTPException(status_code=400, detail="OAuth2 is only applicable to IMAP channels.")
        initiator = s.get(User, user.user_id)
        redirect_uri = f"{settings.public_base_url}/api/channels/oauth-callback/"
        try:
            auth_url, state = get_authorization_url(channel, initiator, redirect_uri=redirect_uri, provider=provider.lower())
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        return {"authorization_url": auth_url, "state": state, "provider": provider.lower()}

    return await run_in_tenant(user, _do, commit=False)


@router.get("/{id}/", response_model=ChannelResponse)
async def get_channel(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Retrieve a single communication channel (managers only)."""
    _require_manager(user)

    def _do(s):
        c = s.scalars(select(Channel).where(Channel.id == id, Channel.tenant_id == user.tenant_id)).first()
        if not c:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")
        return channel_response(c)

    return await run_in_tenant(user, _do, commit=False)
