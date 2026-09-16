"""
Browser-based Meta OAuth flow for connecting Instagram Business accounts.

Flow:

    GET /auth/meta/login
        -> create an OAuth state
        -> redirect the user to Meta's consent screen

    GET /auth/meta/callback
        -> validate and consume the OAuth state
        -> exchange the authorization code for a short-lived user token
        -> exchange that token for a long-lived user token
        -> discover the Facebook Pages managed by the user
        -> inspect each Page for a linked Instagram Business account
        -> store the required platform credentials through the secrets
           storage abstraction
        -> create or update one SocialAccount row per Instagram account

Meta's account model is Page-based: the Instagram Business account is
discovered through the Facebook Page to which it is linked.

OAuth credentials and access tokens are not stored directly in
SocialAccount. The secrets service owns credential storage; this router
stores only the returned secret identifier in SocialAccount.secrets_manager_arn.
"""

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import get_db
from app.services import secrets_service


router = APIRouter(
    prefix="/auth/meta",
    tags=["auth"],
)


# Keep this version aligned with the Graph API endpoints currently used by
# this integration. Platform/API-version upgrades should be treated as an
# explicit integration change, not as a documentation cleanup.
GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
    "pages_show_list",
    "pages_read_engagement",
]


# Pending OAuth state is kept in process memory for the current local /
# single-process implementation. A multi-instance deployment needs shared
# state so the callback can be handled by any application instance.
_pending_states: set[str] = set()


@router.get("/login")
def meta_login():
    """Start the Meta OAuth authorization flow."""
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise HTTPException(
            status_code=500,
            detail="META_APP_ID / META_APP_SECRET not set in .env",
        )

    state = os.urandom(16).hex()
    _pending_states.add(state)

    auth_url = (
        f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"
        f"?client_id={settings.meta_app_id}"
        f"&redirect_uri={settings.meta_redirect_uri}"
        f"&state={state}"
        f"&scope={','.join(SCOPES)}"
    )

    return RedirectResponse(auth_url)


@router.get("/callback")
async def meta_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """Complete the OAuth flow and connect discovered Instagram accounts."""
    state = request.query_params.get("state")
    code = request.query_params.get("code")

    # Consume the state before processing the callback so the same state
    # cannot be reused within this application process.
    if state not in _pending_states:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown or expired OAuth state — "
                "try /auth/meta/login again"
            ),
        )

    _pending_states.discard(state)

    if not code:
        error_desc = request.query_params.get(
            "error_description",
            "No code returned",
        )
        raise HTTPException(
            status_code=400,
            detail=f"Meta OAuth failed: {error_desc}",
        )

    async with httpx.AsyncClient() as client:
        # Exchange the authorization code for a short-lived user token.
        token_res = await client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri,
                "code": code,
            },
        )
        token_res.raise_for_status()
        short_lived_token = token_res.json()["access_token"]

        # Exchange the short-lived user token for a long-lived user token.
        long_lived_res = await client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
        long_lived_res.raise_for_status()
        user_token = long_lived_res.json()["access_token"]

        # Discover every Facebook Page managed by this Meta user.
        pages_res = await client.get(
            f"{GRAPH_BASE}/me/accounts",
            params={
                "access_token": user_token,
                "fields": "id,name,access_token",
            },
        )
        pages_res.raise_for_status()
        pages = pages_res.json().get("data", [])

        if not pages:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No Facebook Pages found for this account. "
                    "Make sure TV9's Facebook account manages at least one Page."
                ),
            )

        connected = []

        for page in pages:
            page_token = page["access_token"]

            # A Page can expose a linked Instagram Business account.
            ig_res = await client.get(
                f"{GRAPH_BASE}/{page['id']}",
                params={
                    "fields": "instagram_business_account",
                    "access_token": page_token,
                },
            )
            ig_res.raise_for_status()
            ig_data = ig_res.json().get("instagram_business_account")

            if not ig_data:
                # This Page has no linked Instagram Business account.
                continue

            ig_user_id = ig_data["id"]

            # Resolve a human-readable Instagram username.
            username_res = await client.get(
                f"{GRAPH_BASE}/{ig_user_id}",
                params={
                    "fields": "username",
                    "access_token": page_token,
                },
            )
            username_res.raise_for_status()

            ig_username = username_res.json().get(
                "username",
                ig_user_id,
            )

            # Keep platform credentials out of the relational database.
            secret_key = f"instagram:{ig_user_id}"

            secrets_service.save_secret(
                secret_key,
                {
                    "page_access_token": page_token,
                    "ig_user_id": ig_user_id,
                    "page_id": page["id"],
                },
            )

            existing = (
                db.query(models.SocialAccount)
                .filter(
                    models.SocialAccount.platform == "instagram",
                    models.SocialAccount.account_name == ig_username,
                )
                .first()
            )

            if existing:
                existing.secrets_manager_arn = secret_key
                existing.is_active = True
            else:
                db.add(
                    models.SocialAccount(
                        platform="instagram",
                        account_name=ig_username,
                        secrets_manager_arn=secret_key,
                    )
                )

            connected.append(ig_username)

        db.commit()

    if not connected:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Instagram Business accounts found. Confirm TV9's "
                "Instagram is a Business/Creator account linked to a "
                "Facebook Page."
            ),
        )

    return {
        "status": "connected",
        "instagram_accounts": connected,
        "note": (
            "These Instagram accounts are now available as targets "
            "when creating posts."
        ),
    }
