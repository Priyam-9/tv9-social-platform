"""
Session-based authentication. get_current_user reads the signed
session cookie set by POST /auth/login and looks up the matching
Session row (via its hash) in Postgres — X-User-Email is no longer
trusted on ordinary requests, only at the login step itself (see
app/routers/auth_session.py).

SCOPE NOTE: this does not verify that the person who called /login
actually is who they claimed — X-User-Email is still trusted at face
value at that one endpoint, with no password or second factor. What
this closes is re-trusting that claim on every subsequent request:
before this, any request carrying a spoofed X-User-Email header got
scoped access; now that only works once, at /login, and everything
after runs off a signed, DB-backed session token instead. This is
interim hardening, not real authentication — real login (password or
Google OAuth) is still needed before wider rollout or AWS deployment.

Requests with NO session cookie at all are treated as unrestricted
(equivalent to admin), preserving the system's pre-existing fallback
behavior for local dev/testing and anything not yet updated to log in.
A cookie that WAS sent but doesn't match a live session (expired,
logged out, tampered) is treated differently — see get_current_user.
"""

import uuid
from datetime import datetime, timezone

from fastapi import Header, HTTPException, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app import models
from app.routers.auth_session import SESSION_COOKIE_NAME


def verify_api_key(x_api_key: str = Header(default=None)):
    if not settings.api_access_key:
        # Fails loudly rather than silently allowing open access if
        # someone forgets to set a key outside local dev.
        if settings.environment != "local":
            raise HTTPException(
                status_code=500,
                detail="API_ACCESS_KEY is not configured. Refusing to run unauthenticated outside local dev.",
            )
        return  # local dev with no key set — allowed, but logged via the warning below at startup

    if x_api_key != settings.api_access_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> "models.User | None":
    """
    Returns the User tied to the session cookie, or None if no cookie
    was sent at all (treated as unrestricted access, see module
    docstring). Raises 401 if a cookie was sent but doesn't match a
    live session — distinguishing "never logged in" (fine, falls back
    to unrestricted) from "you HAD a session and it's now gone"
    (invalid/expired/logged-out — the client should be told, not
    silently handed admin access back).

    Uses `raw_token is None` rather than `not raw_token` deliberately:
    logout() sends an empty-string cookie (via delete_cookie), and an
    empty string is falsy in Python — `not raw_token` would treat that
    the same as "no cookie sent," silently falling back to
    unrestricted access right after logout. Checking `is None`
    specifically means an empty-string cookie still gets looked up
    below, fails to match any session, and correctly raises 401.
    """
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token is None:
        return None

    token_hash = models.hash_session_token(raw_token)
    session = db.query(models.Session).filter(models.Session.token_hash == token_hash).first()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session — please log in again")

    if session.expires_at < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired — please log in again")

    return session.user


def get_accessible_account_ids(user: "models.User | None", db: Session) -> set[uuid.UUID] | None:
    """
    Returns the set of social_account IDs `user` is allowed to see/use,
    or None if they should see everything (no user context sent, or
    they're an admin) — None is a deliberate "no filter" signal, not
    an empty-access signal.
    """
    if user is None or user.is_admin:
        return None
    rows = (
        db.query(models.UserAccountAccess.social_account_id)
        .filter(models.UserAccountAccess.user_id == user.id)
        .all()
    )
    return {r[0] for r in rows}