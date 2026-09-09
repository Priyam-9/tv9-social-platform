"""
Session-based authentication. get_current_user reads the signed
session cookie set by POST /auth/login and looks up the matching
Session row (via its hash) in Postgres — X-User-Email is no longer
trusted on ordinary requests, only at the login step itself.
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
        if settings.environment != "local":
            raise HTTPException(
                status_code=500,
                detail="API_ACCESS_KEY is not configured. Refusing to run unauthenticated outside local dev.",
            )
        return

    if x_api_key != settings.api_access_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> "models.User | None":
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
    if user is None or user.is_admin:
        return None
    rows = (
        db.query(models.UserAccountAccess.social_account_id)
        .filter(models.UserAccountAccess.user_id == user.id)
        .all()
    )
    return {r[0] for r in rows}