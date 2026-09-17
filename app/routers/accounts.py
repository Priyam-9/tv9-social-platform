"""
Manage connected social accounts.

The generic account-creation endpoint remains administrator-only because it
accepts an internal secrets pointer directly. Platform-specific connection
flows use dedicated endpoints so users never need to paste secrets into the
relational database or into dashboard configuration.

Currently the Telegram connection flow accepts a bot token and chat ID,
validates them against the Telegram Bot API, stores the credentials through
secrets_service, and grants the newly connected account to the authenticated
user. YouTube continues to use its existing OAuth flow.
"""

from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import (
    get_accessible_account_ids,
    get_current_user,
    require_admin,
    verify_api_key,
)
from app.services import secrets_service
from app.services.content_limits import normalize_language, validate_language


router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(verify_api_key)],
)


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_VALIDATION_TIMEOUT_SECONDS = 10


class TelegramConnectRequest(BaseModel):
    """Credentials and metadata required to connect one Telegram target."""

    account_name: str | None = Field(
        default=None,
        max_length=255,
        description="Display name shown in the dashboard.",
    )
    bot_token: str = Field(
        min_length=1,
        max_length=512,
        description="Bot token created through @BotFather.",
    )
    chat_id: str = Field(
        min_length=1,
        max_length=255,
        description="Telegram channel/group chat ID, for example -1001234567890.",
    )
    default_language: str | None = Field(
        default=None,
        max_length=10,
        description="Optional default language code such as hi or mr.",
    )


def _telegram_api_get(
    bot_token: str,
    method: str,
    *,
    params: dict[str, str] | None = None,
) -> dict:
    """Call a Telegram Bot API read endpoint without exposing secrets."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"

    try:
        response = requests.get(
            url,
            params=params,
            timeout=TELEGRAM_VALIDATION_TIMEOUT_SECONDS,
        )
        data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Telegram validation request failed: {type(exc).__name__}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Telegram validation returned an invalid response",
        ) from exc

    if not data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=data.get("description", "Telegram rejected the supplied credentials"),
        )

    return data


def _resolve_telegram_connection(
    bot_token: str,
    chat_id: str,
) -> str:
    """Validate the bot and chat, then return a useful display name."""
    bot_data = _telegram_api_get(bot_token, "getMe")
    _telegram_api_get(
        bot_token,
        "getChat",
        params={"chat_id": chat_id},
    )

    bot_username = (bot_data.get("result") or {}).get("username")
    return bot_username or "Telegram bot"


def _grant_account_to_user(
    account: models.SocialAccount,
    user: models.User,
    db: Session,
) -> None:
    """Grant a connected account to the user who completed the flow."""
    existing = (
        db.query(models.UserAccountAccess)
        .filter(
            models.UserAccountAccess.user_id == user.id,
            models.UserAccountAccess.social_account_id == account.id,
        )
        .first()
    )

    if existing is None:
        db.add(
            models.UserAccountAccess(
                user_id=user.id,
                social_account_id=account.id,
            )
        )


@router.post(
    "/",
    response_model=schemas.SocialAccountOut,
)
def create_account(
    payload: schemas.SocialAccountCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    """Create a new connected social account as an administrator."""
    account = models.SocialAccount(
        **payload.model_dump()
    )

    db.add(account)
    db.commit()
    db.refresh(account)

    return account


@router.post(
    "/telegram/connect",
    response_model=schemas.SocialAccountOut,
)
def connect_telegram_account(
    payload: TelegramConnectRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Connect a Telegram channel or group using its bot credentials.

    The bot token is validated through the Telegram Bot API and then stored
    through the secrets abstraction. Only the secrets pointer is persisted in
    SocialAccount. The connected account is automatically granted to the user
    who completed the connection.
    """
    bot_token = payload.bot_token.strip()
    chat_id = payload.chat_id.strip()

    if not bot_token or not chat_id:
        raise HTTPException(
            status_code=400,
            detail="bot_token and chat_id are required",
        )

    normalized_language = normalize_language(payload.default_language)
    language_violations = validate_language(normalized_language)
    if language_violations:
        raise HTTPException(
            status_code=400,
            detail=language_violations,
        )

    bot_username = _resolve_telegram_connection(
        bot_token,
        chat_id,
    )

    requested_name = (payload.account_name or "").strip()
    account_name = requested_name or bot_username

    # Persist the bot credentials outside the relational database. Using the
    # chat ID in the logical key keeps repeated connections to the same target
    # on one secret in local development and in the production secrets backend.
    secret_key = f"telegram:{chat_id}"
    secret_pointer = secrets_service.save_secret(
        secret_key,
        {
            "bot_token": bot_token,
            "chat_id": chat_id,
        },
    )

    # The secret pointer is the stable identity for a connected Telegram target
    # because it is derived from the Telegram chat ID. Prefer it over the
    # dashboard display name so reconnecting the same chat after renaming it
    # updates the existing SocialAccount instead of creating a duplicate.
    account = (
        db.query(models.SocialAccount)
        .filter(
            models.SocialAccount.platform == "telegram",
            models.SocialAccount.secrets_manager_arn == secret_pointer,
        )
        .first()
    )

    # Backward-compatibility path for older local rows created before stable
    # secret-pointer lookup was introduced. Once found, the row is normalized
    # to the current stable pointer above.
    if account is None:
        account = (
            db.query(models.SocialAccount)
            .filter(
                models.SocialAccount.platform == "telegram",
                models.SocialAccount.account_name == account_name,
            )
            .first()
        )

    now = datetime.now(timezone.utc)

    if account is None:
        account = models.SocialAccount(
            platform="telegram",
            account_name=account_name,
            secrets_manager_arn=secret_pointer,
            is_active=True,
            connected_at=now,
            default_language=normalized_language,
        )
        db.add(account)
        db.flush()
    else:
        account.secrets_manager_arn = secret_pointer
        account.is_active = True
        account.connected_at = now
        account.default_language = normalized_language

    _grant_account_to_user(
        account,
        current_user,
        db,
    )

    db.commit()
    db.refresh(account)

    return account


@router.get(
    "/",
    response_model=list[schemas.SocialAccountOut],
)
def list_accounts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    List accounts visible to the authenticated user.

    Administrators receive all accounts. Content managers receive only
    accounts explicitly granted through UserAccountAccess.
    """
    accessible = get_accessible_account_ids(
        current_user,
        db,
    )

    # Only active accounts are returned as publishing targets. Inactive
    # accounts are retained in the database for historical post/target records.
    query = db.query(models.SocialAccount).filter(
        models.SocialAccount.is_active.is_(True)
    )

    if accessible is not None:
        query = query.filter(
            models.SocialAccount.id.in_(accessible)
        )

    return (
        query
        .order_by(
            models.SocialAccount.account_name.asc()
        )
        .all()
    )
