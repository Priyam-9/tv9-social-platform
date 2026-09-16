"""
Authentication, role, and account-access dependencies for the TV9 Publisher.

Authentication is session-based:

    POST /auth/login
        -> verifies email + password
        -> creates a DB-backed session
        -> sends the raw session token as an HttpOnly cookie

Every protected endpoint then resolves the current user from that cookie.
Client-supplied identity headers such as X-User-Email are never trusted.

The API key remains a separate application-level protection layer.

Roles:
    admin
        Full account visibility and administrative access.

    content_manager
        Access only to explicitly granted social accounts through
        UserAccountAccess.

`User.role` is the authoritative application role. The legacy `is_admin`
field is retained on the model for backward compatibility, but it is not
used for authorization decisions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import get_db
from app.routers.auth_session import SESSION_COOKIE_NAME


ROLE_ADMIN = "admin"
ROLE_CONTENT_MANAGER = "content_manager"

VALID_ROLES = {
    ROLE_ADMIN,
    ROLE_CONTENT_MANAGER,
}


def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    Validate the application API key.

    In local development an unset API key is allowed for convenience.
    Outside local development, the application refuses to run without one.
    """
    if not settings.api_access_key:
        if settings.environment != "local":
            raise HTTPException(
                status_code=500,
                detail=(
                    "API_ACCESS_KEY is not configured. "
                    "Refusing to run unauthenticated outside local dev."
                ),
            )
        return

    if x_api_key != settings.api_access_key:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-API-Key header",
        )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> models.User:
    """
    Resolve the authenticated user from the DB-backed session cookie.

    A missing, invalid, expired, or misconfigured session is an
    authentication failure. There is intentionally no anonymous-user
    fallback.
    """
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)

    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required — please log in",
        )

    token_hash = models.hash_session_token(raw_token)

    session = (
        db.query(models.Session)
        .filter(models.Session.token_hash == token_hash)
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=401,
            detail="Invalid session — please log in again",
        )

    now = datetime.now(timezone.utc)

    if session.expires_at <= now:
        db.delete(session)
        db.commit()
        raise HTTPException(
            status_code=401,
            detail="Session expired — please log in again",
        )

    user = session.user

    if user is None:
        db.delete(session)
        db.commit()
        raise HTTPException(
            status_code=401,
            detail="Invalid session — please log in again",
        )

    # Role is authoritative. Never silently promote a malformed role using
    # the legacy is_admin field.
    if user.role not in VALID_ROLES:
        raise HTTPException(
            status_code=403,
            detail="User account has an invalid role configuration",
        )

    return user


def is_admin(user: models.User) -> bool:
    """Return whether the authenticated user has the admin role."""
    return user.role == ROLE_ADMIN


def require_admin(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Require the authenticated user to have the admin role."""
    if not is_admin(current_user):
        raise HTTPException(
            status_code=403,
            detail="Administrator permissions required",
        )

    return current_user


def get_accessible_account_ids(
    user: models.User,
    db: Session,
) -> set[uuid.UUID] | None:
    """
    Return the social-account IDs the authenticated user may access.

    None means unrestricted access and is reserved for administrators.
    Ordinary content managers receive the explicit set of grants from
    UserAccountAccess, including an empty set when they have no grants.
    """
    if is_admin(user):
        return None

    rows = (
        db.query(models.UserAccountAccess.social_account_id)
        .filter(models.UserAccountAccess.user_id == user.id)
        .all()
    )

    return {row[0] for row in rows}
