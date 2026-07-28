"""
Simple API key authentication. Every endpoint that can read or modify
data (accounts, posts, publishing) requires a valid X-API-Key header
matching API_ACCESS_KEY in .env.

This is intentionally simple — a single shared key, not per-user login.
It closes the "anyone on the network can hit the API" gap identified in
the security review.

On top of the API key, an optional X-User-Email header identifies WHICH
person is making the request, for the Admin vs Content Manager access
model. This is an interim mechanism — it trusts whatever email the
client sends, which is fine while only the dashboard (running on
trusted internal infrastructure) sends it, but is NOT a substitute for
real login. Real authentication (verifying the person actually is who
they claim) replaces this later; until then, requests with no
X-User-Email header are treated as unrestricted (equivalent to admin),
matching the system's behavior before roles existed — so nothing
already built breaks as this rolls out gradually.
"""

import uuid

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app import models


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


def get_current_user(
    x_user_email: str = Header(default=None), db: Session = Depends(get_db)
) -> "models.User | None":
    """Returns the User matching X-User-Email, or None if the header
    wasn't sent (treated as unrestricted access, see module docstring)."""
    if not x_user_email:
        return None
    user = db.query(models.User).filter(models.User.email == x_user_email).first()
    if not user:
        raise HTTPException(status_code=401, detail=f"No user found for email: {x_user_email}")
    return user


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
