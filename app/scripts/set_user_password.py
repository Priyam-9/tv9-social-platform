"""
One-time utility to set or reset a user's password.

Usage inside the API container:

    python app/scripts/set_user_password.py

The script prompts for the user's email and password. The plaintext password
is never written to the database; only the scrypt-derived password hash is
stored in users.password_hash.

For security, changing a password also revokes every existing DB-backed
session for that user. The user must log in again on every browser/device.
"""

from __future__ import annotations

import getpass
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app import models
from app.services.password_service import (
    hash_password,
    password_meets_policy,
)


def main() -> int:
    email = input("User email: ").strip().lower()

    if not email:
        print("Email is required.")
        return 1

    user_password = getpass.getpass("New password: ")
    confirm_password = getpass.getpass("Confirm password: ")

    if user_password != confirm_password:
        print("Passwords do not match.")
        return 1

    if not password_meets_policy(user_password):
        print("Password must be at least 12 characters long.")
        return 1

    engine = create_engine(settings.database_url)

    try:
        with Session(engine) as db:
            user = (
                db.query(models.User)
                .filter(models.User.email == email)
                .first()
            )

            if not user:
                print(f"No user found for email: {email}")
                return 1

            user.password_hash = hash_password(user_password)

            revoked_sessions = (
                db.query(models.Session)
                .filter(models.Session.user_id == user.id)
                .delete(synchronize_session=False)
            )

            db.commit()

            print(f"Password updated successfully for {user.email}.")
            print(f"Revoked existing sessions: {revoked_sessions}")
            print("The user must log in again on every browser/device.")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
