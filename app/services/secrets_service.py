"""
Secrets storage abstraction.

Locally (ENVIRONMENT=local), tokens are stored in a JSON file on disk —
this is fine for local development ONLY, never for production. In AWS,
this same interface will be backed by Secrets Manager instead (see the
build guide, Phase 1 and Phase 6) — adapters and routers never need to
know which backend is active, they just call save_secret/get_secret.

The "arn" terminology is kept even in local mode so the SocialAccount
model's `secrets_manager_arn` field means the same thing in both
environments — just swap the backend, not the calling code.
"""

import json
import os
from pathlib import Path

from app.config import settings


def _load_local_store() -> dict:
    path = Path(settings.local_secrets_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _save_local_store(store: dict) -> None:
    path = Path(settings.local_secrets_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(store, f, indent=2)


def save_secret(key: str, value: dict) -> str:
    """
    Stores `value` (e.g. OAuth tokens) under `key`. Returns the
    identifier to store on SocialAccount.secrets_manager_arn — locally
    this is just the key; in AWS it'll be the real Secrets Manager ARN.
    """
    if settings.environment == "local":
        store = _load_local_store()
        store[key] = value
        _save_local_store(store)
        return key
    raise NotImplementedError(
        "AWS Secrets Manager backend not wired up yet — this gets added in the AWS deployment phase."
    )


def get_secret(arn: str) -> dict:
    """Retrieves a previously saved secret by its identifier."""
    if settings.environment == "local":
        store = _load_local_store()
        if arn not in store:
            raise KeyError(f"No local secret found for key: {arn}")
        return store[arn]
    raise NotImplementedError(
        "AWS Secrets Manager backend not wired up yet — this gets added in the AWS deployment phase."
    )
