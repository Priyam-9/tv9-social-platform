"""
Secrets storage abstraction.

Local development:
    ENVIRONMENT=local
        -> secrets are stored in the configured JSON file.

Non-local environments:
    -> secrets are stored in AWS Secrets Manager.

The rest of the application uses the same save_secret/get_secret interface
regardless of environment.

Logical identifiers such as "youtube:CHANNEL_ID" are valid in the
application, but ':' is not allowed in an AWS Secrets Manager secret name.
Therefore logical identifiers are deterministically encoded to safe AWS
secret names. Full AWS Secrets Manager ARNs are accepted as-is.

Secret values are never written to logs or included in raised error
messages.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import settings


_AWS_LOGICAL_PREFIX = "tv9/managed/"
_AWS_SECRET_NAME_MAX_LENGTH = 512


def _validate_secret_value(value: dict) -> dict:
    """Validate that a secret payload is a JSON object."""
    if not isinstance(value, dict):
        raise TypeError("Secret value must be a dictionary.")

    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Secret value must contain only JSON-serializable data."
        ) from exc

    return value


def _load_local_store() -> dict:
    """Load the local development secret store."""
    path = Path(settings.local_secrets_path)

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            store = json.load(f)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Local secrets store is not valid JSON: {path}"
        ) from exc

    if not isinstance(store, dict):
        raise RuntimeError("Local secrets store must contain a JSON object.")

    return store


def _save_local_store(store: dict) -> None:
    """
    Save the local secret store safely.

    A temporary file plus os.replace prevents leaving a partially-written
    JSON document behind if the process is interrupted.
    """
    path = Path(settings.local_secrets_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_name(f"{path.name}.tmp")

    try:
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        # Restrict permissions where supported by the operating system.
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass

        os.replace(temp_path, path)

        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _validate_secret_identifier(identifier: str) -> str:
    """Validate a secret identifier before it reaches a storage backend."""
    if not isinstance(identifier, str):
        raise TypeError("Secret identifier must be a string.")

    identifier = identifier.strip()

    if not identifier:
        raise ValueError("Secret identifier must be a non-empty string.")

    if len(identifier) > 2048:
        raise ValueError("Secret identifier is too long.")

    return identifier


def _is_full_aws_secret_arn(identifier: str) -> bool:
    """Return True when identifier is a full AWS Secrets Manager ARN."""
    return identifier.startswith("arn:aws:secretsmanager:")


def _aws_secret_name(identifier: str) -> str:
    """
    Convert a logical application identifier to an AWS-safe secret name.

    AWS secret names allow ASCII letters, numbers, and /_+=.@-. They do not
    allow ':'. Base64 URL-safe encoding gives us a reversible representation
    using only '-' and '_' plus letters/numbers; padding '=' is removed.
    """
    identifier = _validate_secret_identifier(identifier)

    if _is_full_aws_secret_arn(identifier):
        return identifier

    encoded = base64.urlsafe_b64encode(
        identifier.encode("utf-8")
    ).decode("ascii").rstrip("=")

    name = f"{_AWS_LOGICAL_PREFIX}{encoded}"

    if len(name) > _AWS_SECRET_NAME_MAX_LENGTH:
        raise ValueError("Secret identifier is too long for AWS Secrets Manager.")

    return name


def _get_aws_secrets_client():
    """Create an AWS Secrets Manager client using the normal boto3 provider chain."""
    return boto3.client("secretsmanager")


def _put_aws_secret(client, secret_id: str, value: dict) -> None:
    """Update an existing AWS secret."""
    client.put_secret_value(
        SecretId=secret_id,
        SecretString=json.dumps(value, separators=(",", ":")),
    )


def _create_aws_secret(client, secret_name: str, value: dict) -> str:
    """Create a new AWS secret and return its ARN."""
    response = client.create_secret(
        Name=secret_name,
        SecretString=json.dumps(value, separators=(",", ":")),
    )

    arn = response.get("ARN")
    if not arn:
        raise RuntimeError("AWS Secrets Manager did not return a secret ARN.")

    return arn


def _save_aws_secret(identifier: str, value: dict) -> str:
    """
    Create-or-update an AWS secret.

    Logical identifiers are mapped to deterministic AWS-safe names. A
    full ARN is used unchanged.

    The create-on-missing behavior is required for first-time OAuth account
    connections; subsequent token refreshes update the existing secret.
    """
    client = _get_aws_secrets_client()
    secret_id = _aws_secret_name(identifier)

    try:
        _put_aws_secret(client, secret_id, value)
        return identifier
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code != "ResourceNotFoundException":
            raise RuntimeError(
                "Failed to store secret in AWS Secrets Manager."
            ) from exc

    try:
        _create_aws_secret(client, secret_id, value)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        # Another process may have created the same secret after the first
        # put failed. In that case, retry the update once.
        if error_code == "ResourceExistsException":
            try:
                _put_aws_secret(client, secret_id, value)
                return identifier
            except ClientError as retry_exc:
                raise RuntimeError(
                    "Failed to store secret in AWS Secrets Manager."
                ) from retry_exc

        raise RuntimeError(
            "Failed to create secret in AWS Secrets Manager."
        ) from exc

    return identifier


def save_secret(key: str, value: dict) -> str:
    """
    Store a secret payload and return its logical identifier.

    Local mode keeps the original key exactly as supplied.

    Non-local mode stores the value in AWS Secrets Manager. Existing
    full ARNs are used directly; application-level logical identifiers
    are encoded into valid AWS secret names.
    """
    key = _validate_secret_identifier(key)
    value = _validate_secret_value(value)

    if settings.environment == "local":
        store = _load_local_store()
        store[key] = value
        _save_local_store(store)
        return key

    return _save_aws_secret(key, value)


def _get_aws_secret(identifier: str) -> dict:
    """Retrieve and decode a JSON AWS Secrets Manager secret."""
    client = _get_aws_secrets_client()
    secret_id = _aws_secret_name(identifier)

    try:
        response: dict[str, Any] = client.get_secret_value(
            SecretId=secret_id
        )
    except ClientError as exc:
        raise RuntimeError(
            "Failed to retrieve secret from AWS Secrets Manager."
        ) from exc

    secret_string = response.get("SecretString")

    if secret_string is None:
        raise RuntimeError(
            "AWS secret does not contain a SecretString payload."
        )

    try:
        value = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "AWS secret does not contain valid JSON."
        ) from exc

    return _validate_secret_value(value)


def get_secret(arn: str) -> dict:
    """
    Retrieve a previously saved secret by its logical identifier or ARN.
    """
    arn = _validate_secret_identifier(arn)

    if settings.environment == "local":
        store = _load_local_store()

        if arn not in store:
            raise KeyError(f"No local secret found for key: {arn}")

        return _validate_secret_value(store[arn])

    return _get_aws_secret(arn)
