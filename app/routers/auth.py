"""
Browser-based OAuth flow for connecting a real YouTube account.

Flow:

    GET /auth/youtube/login
        -> build the Google OAuth authorization URL
        -> keep the OAuth state/flow temporarily in memory

    GET /auth/youtube/callback
        -> validate and consume the OAuth state
        -> exchange the authorization code for credentials
        -> look up the authenticated YouTube channel
        -> store credentials through the secrets abstraction
        -> create or update the matching SocialAccount row

The OAuth client secret and resulting credentials are never stored in the
SocialAccount row itself. SocialAccount.secrets_manager_arn contains only
the identifier used by the secrets abstraction.
"""

import os
from datetime import datetime, timezone

from app.config import settings

# OAuth libraries reject plain HTTP by default. This setting is permitted only
# for the local development environment because the local callback runs over
# HTTP. Production deployments must use HTTPS.
if settings.environment == "local":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.services import secrets_service


router = APIRouter(
    prefix="/auth/youtube",
    tags=["auth"],
)


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


# The current implementation keeps pending OAuth flows in process memory.
# This is suitable for the current local/single-process development setup.
# A multi-instance deployment needs a shared state mechanism.
_pending_flows: dict[str, Flow] = {}


def _build_flow() -> Flow:
    """Build a Google OAuth flow from the application's YouTube settings."""
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
        client_config,
        scopes=SCOPES,
        redirect_uri=settings.youtube_redirect_uri,
    )


@router.get("/login")
def youtube_login():
    """Start the browser-based YouTube OAuth authorization flow."""
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
def youtube_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Complete the OAuth flow and connect the returned YouTube channel.

    The OAuth state is consumed before exchanging the authorization code,
    preventing the same pending state from being reused in this process.
    """
    state = request.query_params.get("state")
    flow = _pending_flows.pop(state, None)

    if flow is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown or expired OAuth state — "
                "try /auth/youtube/login again"
            ),
        )

    # google-auth-oauthlib performs the authorization-code exchange here.
    flow.fetch_token(
        authorization_response=str(request.url)
    )
    credentials = flow.credentials

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    channel_response = (
        youtube.channels()
        .list(
            mine=True,
            part="snippet",
        )
        .execute()
    )

    items = channel_response.get("items", [])

    if not items:
        raise HTTPException(
            status_code=400,
            detail="No YouTube channel found on this Google account",
        )

    channel_id = items[0]["id"]
    channel_title = items[0]["snippet"]["title"]

    # Keep OAuth credentials outside the relational database. The secrets
    # abstraction decides whether this identifier maps to local development
    # storage or a deployed secrets backend.
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
        .filter(
            models.SocialAccount.platform == "youtube",
            models.SocialAccount.account_name == channel_title,
        )
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
        "note": (
            "This YouTube account is now available as a target "
            "when creating posts."
        ),
    }
