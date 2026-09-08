"""
Session-based login. IMPORTANT SCOPE NOTE: this does NOT verify that
the person calling /login actually is who they claim — X-User-Email
is still trusted at face value at that one endpoint, with no password
or second factor. What this DOES do: it stops that trust decision
from being re-made on every single request. Before this, any request
carrying a spoofed X-User-Email header got scoped access; now, forging
identity requires hitting /login once, and everything after that runs
off a signed, DB-backed session token instead. This is an interim
hardening step, not real authentication — real login (password or
Google OAuth) is still needed before wider rollout or AWS deployment,
per the original security review.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Response, Request
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.database import get_db
from app import models

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE_NAME = "tv9_session"
SESSION_TTL = timedelta(hours=12)


@router.post("/login")
def login(
    response: Response,
    x_user_email: str = Header(default=None),
    db: DBSession = Depends(get_db),
):
    if not x_user_email:
        raise HTTPException(status_code=400, detail="X-User-Email header required to log in")

    user = db.query(models.User).filter(models.User.email == x_user_email).first()
    if not user:
        raise HTTPException(status_code=401, detail=f"No user found for email: {x_user_email}")

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
        # Automatically HTTPS-only outside local dev — same pattern as
        # the OAUTHLIB_INSECURE_TRANSPORT flag in auth.py. Avoids the
        # class of bug where a manual flip gets forgotten before AWS
        # deployment.
        secure=(settings.environment != "local"),
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return {"status": "logged_in", "user": user.display_name, "is_admin": user.is_admin}


@router.post("/logout")
def logout(request: Request, response: Response, db: DBSession = Depends(get_db)):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        token_hash = models.hash_session_token(raw_token)
        db.query(models.Session).filter(models.Session.token_hash == token_hash).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "logged_out"}