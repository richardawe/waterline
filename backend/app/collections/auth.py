"""Sessions, role dependencies and rate limiting for Collections.

Auth is a bearer token in the Authorization header (kept in the browser's
sessionStorage), never a cookie: the static pages at waterline.ng call the
API at api.waterline.ng, and a header that browsers never attach on their
own makes cross-site request forgery structurally impossible, which is how
this satisfies the CSRF requirement without a token dance."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import crypto
from app.collections.audit import Actor
from app.collections.models import AuthSession, Client, ClientUser, OpsUser
from app.collections.timeutil import utcnow
from app.db import get_db

CLIENT_SESSION_TTL = timedelta(days=7)
OPS_SESSION_TTL = timedelta(hours=12)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def create_session(db: Session, actor_type: str, actor_id: str, ip: str | None) -> str:
    token = crypto.new_token()
    ttl = OPS_SESSION_TTL if actor_type == "ops" else CLIENT_SESSION_TTL
    db.add(AuthSession(token_hash=crypto.hash_token(token), actor_type=actor_type, actor_id=actor_id,
                       expires_at=utcnow() + ttl, ip=ip))
    return token


def end_session(db: Session, request: Request) -> None:
    token = _bearer(request)
    if token:
        row = db.execute(select(AuthSession).where(AuthSession.token_hash == crypto.hash_token(token))).scalar_one_or_none()
        if row:
            db.delete(row)


def end_all_sessions(db: Session, actor_type: str, actor_id: str) -> None:
    for row in db.execute(
        select(AuthSession).where(AuthSession.actor_type == actor_type, AuthSession.actor_id == actor_id)
    ).scalars():
        db.delete(row)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def _session(db: Session, request: Request, actor_type: str) -> AuthSession:
    token = _bearer(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    row = db.execute(select(AuthSession).where(AuthSession.token_hash == crypto.hash_token(token))).scalar_one_or_none()
    if row is None or row.actor_type != actor_type or row.expires_at < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has ended. Please log in again.")
    return row


@dataclass
class ClientContext:
    actor: Actor
    user: ClientUser
    client: Client


@dataclass
class OpsContext:
    actor: Actor
    user: OpsUser


def require_client(request: Request, db: Session = Depends(get_db)) -> ClientContext:
    row = _session(db, request, "client")
    user = db.get(ClientUser, row.actor_id)
    if user is None or not user.is_active or not user.email_verified_at:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    actor = Actor(type="client", id=user.id, ip=client_ip(request), client_id=user.client_id, role=user.role)
    return ClientContext(actor=actor, user=user, client=user.client)


def require_owner(ctx: ClientContext = Depends(require_client)) -> ClientContext:
    if ctx.user.role != "Owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can do this.")
    return ctx


def require_ops(request: Request, db: Session = Depends(get_db)) -> OpsContext:
    row = _session(db, request, "ops")
    user = db.get(OpsUser, row.actor_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    return OpsContext(actor=Actor(type="ops", id=user.id, ip=client_ip(request), role=user.role), user=user)


def require_ops_admin(ctx: OpsContext = Depends(require_ops)) -> OpsContext:
    if ctx.user.role != "Admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Waterline Admin only.")
    return ctx


# ---------------------------------------------------------------- rate limiting

# In-process sliding window. Passenger can run more than one worker, so the
# effective limit is per worker — fine as abuse damping at phase-1 volumes;
# move to a shared store if Collections ever runs on several hosts.
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(bucket: str, limit: int, window_seconds: int):
    def dependency(request: Request) -> None:
        key = f"{bucket}:{client_ip(request)}"
        now = time.monotonic()
        hits = _hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please wait a few minutes and try again.")
        hits.append(now)

    return dependency


def reset_rate_limits() -> None:
    _hits.clear()
