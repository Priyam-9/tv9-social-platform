"""
Handles the browser-based OAuth flow for connecting a real YouTube
account. Visit /auth/youtube/login to start it, Google will redirect
back to /auth/youtube/callback once the user approves access, at which
point we exchange the code for tokens, look up the channel, and store
(or update) the SocialAccount row.
"""


import os
from datetime import datetime, timezone

from app.config import settings

if settings.environment == "local":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services import secrets_service

router = APIRouter(prefix="/auth/youtube", tags=["auth"])

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

_pending_flows: dict[str, Flow] = {}


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.youtube_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=settings.youtube_redirect_uri
    )


@router.get("/login")
def youtube_login():
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise HTTPException(
            status_code=500,
            detail="YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET not set in .env",
        )

    flow = _build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _pending_flows[state] = flow
    return RedirectResponse(auth_url)


@router.get("/callback")
def youtube_callback(request: Request, db: Session = Depends(get_db)):
    state = request.query_params.get("state")
    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise HTTPException(status_code=400, detail="Unknown or expired OAuth state — try /auth/youtube/login again")

    flow.fetch_token(authorization_response=str(request.url))
    credentials = flow.credentials

    youtube = build("youtube", "v3", credentials=credentials)
    channel_response = youtube.channels().list(mine=True, part="snippet").execute()
    items = channel_response.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No YouTube channel found on this Google account")

    channel_id = items[0]["id"]
    channel_title = items[0]["snippet"]["title"]

    secret_key = f"youtube:{channel_id}"
    secrets_service.save_secret(
        secret_key,
        {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        },
    )

    now = datetime.now(timezone.utc)

    existing = (
        db.query(models.SocialAccount)
        .filter(models.SocialAccount.platform == "youtube", models.SocialAccount.account_name == channel_title)
        .first()
    )
    if existing:
        existing.secrets_manager_arn = secret_key
        existing.is_active = True
        existing.connected_at = now
    else:
        db.add(
            models.SocialAccount(
                platform="youtube",
                account_name=channel_title,
                secrets_manager_arn=secret_key,
                connected_at=now,
            )
        )
    db.commit()

    return {
        "status": "connected",
        "channel_id": channel_id,
        "channel_title": channel_title,
        "note": "This YouTube account is now available as a target when creating posts.",
    }