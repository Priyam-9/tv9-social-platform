"""
Password-authenticated session login for the TV9 Publisher.

Login requires an email address and password. The password is verified against
the scrypt hash stored on User.password_hash, then a short-lived DB-backed
session is created.

The raw session token is sent to the browser as an HttpOnly cookie. Only a
hash of that token is stored in the database.

The authenticated user's role is returned to the dashboard for display, but
authorization is enforced server-side by the role-aware dependencies.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app import models
from app.config import settings
from app.database import get_db
from app.services.password_service import verify_password


router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE_NAME = "tv9_session"
SESSION_TTL = timedelta(hours=12)

# Keep these constants local to this module. dependencies.py imports
# SESSION_COOKIE_NAME from here, so importing role constants from
# dependencies.py would create a circular import.
ROLE_ADMIN = "admin"
ROLE_CONTENT_MANAGER = "content_manager"
VALID_ROLES = {
    ROLE_ADMIN,
    ROLE_CONTENT_MANAGER,
}


class LoginRequest(BaseModel):
    """Credentials submitted to the login endpoint."""

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(
    credentials: LoginRequest,
    response: Response,
    db: DBSession = Depends(get_db),
):
    email = credentials.email.strip().lower()

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Email is required",
        )

    user = (
        db.query(models.User)
        .filter(models.User.email == email)
        .first()
    )

    # Use the same public error for an unknown user and a bad password so the
    # endpoint does not disclose which email addresses exist.
    if not user or not user.password_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    if not verify_password(
        credentials.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    # Fail closed if a user row contains an unsupported role.
    if user.role not in VALID_ROLES:
        raise HTTPException(
            status_code=403,
            detail="User account has an invalid role configuration",
        )

    raw_token = models.generate_session_token()

    session = models.Session(
        user_id=user.id,
        token_hash=models.hash_session_token(raw_token),
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
    )

    db.add(session)
    db.commit()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        samesite="lax",
        # HTTPS-only outside local development.
        secure=(settings.environment != "local"),
        max_age=int(SESSION_TTL.total_seconds()),
    )

    return {
        "status": "logged_in",
        "user": user.display_name,
        "role": user.role,
        "is_admin": user.role == ROLE_ADMIN,
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)

    if raw_token:
        token_hash = models.hash_session_token(raw_token)

        db.query(models.Session).filter(
            models.Session.token_hash == token_hash
        ).delete()

        db.commit()

    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=(settings.environment != "local"),
    )

    return {"status": "logged_out"}
