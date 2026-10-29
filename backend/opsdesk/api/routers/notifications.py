"""Notifications, notification preferences and the live event stream.

- Any signed-in role may use these routes, but every query is scoped to the caller's own user_id *and* tenant_id,
  inside the signed tenant context so forced RLS applies.
- The notification list is capped at 50.
- Preference reads have no side effects: a user without a stored record gets the defaults (everything on).
- The event stream accepts the Bearer header only (no ?token= query parameter).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload

from ...db.models import Notification, NotificationPreference, Ticket, User
from ...services.visibility import visible_ticket_criteria
from ..repositories.tickets import _iso
from ..runtime import run_in_tenant
from ..security import AuthenticatedUser, get_authenticated_user

router = APIRouter(tags=["notifications"])

LIST_LIMIT = 50
PREFERENCE_FIELDS = ("email_enabled", "in_app_enabled", "notify_on_assignment", "notify_on_replies",
                     "notify_on_approvals", "notify_on_sla")
DEFAULT_PREFERENCES = {f: True for f in PREFERENCE_FIELDS}
STREAM_MAX_SECONDS = 30      # the connection closes after 30s; the client reconnects
STREAM_POLL_SECONDS = 3
STREAM_BATCH = 20


def _active_user(s, user: AuthenticatedUser) -> User:
    """The caller's row in their own tenant. A deleted or deactivated account is refused even if its token has not
    expired yet."""
    u = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id,
                                     User.is_active.is_(True))).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or not found.")
    return u


def _own(user: AuthenticatedUser):
    return (Notification.tenant_id == user.tenant_id, Notification.recipient_id == user.user_id)


def _serialize(n: Notification) -> Dict[str, Any]:
    return {
        "id": n.id, "event_type": n.event_type, "title": n.title, "body": n.body, "ticket": n.ticket_id,
        "ticket_number": n.ticket.number if n.ticket_id else None,
        "actor": {"id": n.actor_id, "name": n.actor.display_name} if n.actor_id else None,
        "is_read": n.is_read, "created_at": _iso(n.created_at),
    }


def _unread(s, user) -> int:
    return s.scalar(select(func.count()).select_from(Notification).where(*_own(user), Notification.is_read.is_(False)))


def _preferences_dict(prefs: Optional[NotificationPreference]) -> Dict[str, bool]:
    if prefs is None:
        return dict(DEFAULT_PREFERENCES)  # model defaults, without creating a row (side-effect-free reads)
    return {f: getattr(prefs, f) for f in PREFERENCE_FIELDS}


@router.get("/api/notifications/")
async def list_notifications(user: AuthenticatedUser = Depends(get_authenticated_user)):
    def _list(s):
        _active_user(s, user)
        rows = s.scalars(select(Notification).options(joinedload(Notification.actor), joinedload(Notification.ticket))
                         .where(*_own(user)).order_by(Notification.created_at.desc(), Notification.id.desc())
                         .limit(LIST_LIMIT)).unique()
        return {"results": [_serialize(n) for n in rows], "unread_count": _unread(s, user)}

    return await run_in_tenant(user, _list, commit=False)


@router.get("/api/notifications/unread_count/")
async def unread_count(user: AuthenticatedUser = Depends(get_authenticated_user)):
    def _count(s):
        _active_user(s, user)
        return {"unread_count": _unread(s, user)}

    return await run_in_tenant(user, _count, commit=False)


@router.post("/api/notifications/{notification_id}/read/")
async def mark_read(notification_id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Idempotent. Someone else's notification, or one that doesn't exist, is a 404 either way, so the response
    reveals nothing about other users' data."""
    def _read(s):
        _active_user(s, user)
        if not s.scalars(select(Notification.id).where(*_own(user), Notification.id == notification_id)).first():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
        s.execute(update(Notification).where(*_own(user), Notification.id == notification_id,
                                             Notification.is_read.is_(False)).values(is_read=True))
        return {"id": notification_id, "is_read": True}

    return await run_in_tenant(user, _read)


@router.post("/api/notifications/mark_all_read/")
async def mark_all_read(user: AuthenticatedUser = Depends(get_authenticated_user)):
    def _all(s):
        _active_user(s, user)
        res = s.execute(update(Notification).where(*_own(user), Notification.is_read.is_(False)).values(is_read=True))
        return {"marked_read": res.rowcount}

    return await run_in_tenant(user, _all)


@router.get("/api/notifications/preferences/")
async def get_preferences(user: AuthenticatedUser = Depends(get_authenticated_user)):
    def _get(s):
        _active_user(s, user)
        return _preferences_dict(s.scalars(select(NotificationPreference).where(
            NotificationPreference.tenant_id == user.tenant_id, NotificationPreference.user_id == user.user_id)).first())

    return await run_in_tenant(user, _get, commit=False)


@router.patch("/api/notifications/preferences/")
async def update_preferences(payload: Any = Body(...), user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Partial update of the six settings. Values must be JSON booleans; unknown keys are rejected. Validation happens
    before any write, and the write is one transaction, so a failed request leaves the stored state unchanged."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected a JSON object.")
    unknown = sorted(set(payload) - set(PREFERENCE_FIELDS))
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"unknown_fields": unknown})
    invalid = sorted(k for k, v in payload.items() if not isinstance(v, bool))
    if invalid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={k: "Must be true or false." for k in invalid})

    def _update(s):
        _active_user(s, user)
        prefs = s.scalars(select(NotificationPreference).where(
            NotificationPreference.tenant_id == user.tenant_id, NotificationPreference.user_id == user.user_id)
            .with_for_update()).first()
        if prefs is None:
            if not payload:
                return _preferences_dict(None)
            prefs = NotificationPreference(tenant_id=user.tenant_id, user_id=user.user_id, **DEFAULT_PREFERENCES)
            s.add(prefs)
        for k, v in payload.items():
            setattr(prefs, k, v)
        s.flush()
        return _preferences_dict(prefs)

    return await run_in_tenant(user, _update)


# --------------------------------------------------------------------------- event stream

def _authorized_for_event(s, me: User, n: Notification) -> bool:
    """The linked ticket must still be visible to the recipient, and internal-note events are withheld from non-staff."""
    if n.ticket_id and not s.scalars(select(Ticket.id).where(Ticket.id == n.ticket_id, visible_ticket_criteria(s, me))).first():
        return False
    if "internal note" in (n.title or "").lower() and not me.is_agent:
        return False
    return True


def _event_frame(n: Notification) -> str:
    payload = json.dumps({"id": n.id, "type": n.event_type, "title": n.title, "ticket_id": n.ticket_id,
                          "ts": _iso(n.created_at)})
    return f"id: {n.id}\nevent: notification\ndata: {payload}\n\n"


def _poll(s, user: AuthenticatedUser, after_id: int, latest_only: bool):
    """One short tenant-scoped transaction per poll, so no connection is held open for the life of the stream.
    Returns (still_active, frames, new_last_id)."""
    me = s.scalars(select(User).options(joinedload(User.tenant)).where(
        User.id == user.user_id, User.tenant_id == user.tenant_id, User.is_active.is_(True))).first()
    if me is None:
        return False, [], after_id
    if latest_only:
        latest = s.scalar(select(func.max(Notification.id)).where(*_own(user)))
        return True, [], latest or 0
    frames, last = [], after_id
    for n in s.scalars(select(Notification).where(*_own(user), Notification.id > after_id)
                       .order_by(Notification.id).limit(STREAM_BATCH)):
        last = max(last, n.id)  # advance past withheld events too, so they are never re-sent
        if _authorized_for_event(s, me, n):
            frames.append(_event_frame(n))
    return True, frames, last


@router.get("/api/events/stream/")
async def event_stream(request: Request, last_event_id: Optional[str] = Query(None), once: Optional[str] = Query(None),
                       last_event_id_header: Optional[str] = Header(None, alias="Last-Event-ID"),
                       user: AuthenticatedUser = Depends(get_authenticated_user)):
    raw = last_event_id_header or last_event_id
    try:
        start_id = max(0, int(raw)) if raw else 0
    except (TypeError, ValueError):
        start_id = 0

    def poll(after_id: int, latest_only: bool):
        return run_in_tenant(user, lambda s: _poll(s, user, after_id, latest_only), commit=False)

    active, _, _ = await poll(0, True)  # refuse before streaming starts if the account is gone or inactive
    if not active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or not found.")

    async def frames():
        yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"
        ok, out, last = await (poll(start_id, False) if start_id > 0 else poll(0, True))  # replay, or start "now"
        if not ok:
            return
        for f in out:
            yield f
        if once == "1":
            return
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            await asyncio.sleep(STREAM_POLL_SECONDS)
            ok, out, last = await poll(last, False)
            if not ok:  # deactivated mid-stream: stop delivering immediately
                return
            if out:
                for f in out:
                    yield f
            else:
                yield ": ping\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
