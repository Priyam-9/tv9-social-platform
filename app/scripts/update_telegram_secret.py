"""
Updates an existing Telegram account's stored bot_token/chat_id in
place — e.g. after discovering the token was copied incorrectly.
Unlike register_telegram_account.py, this does NOT create a new
SocialAccount row; it overwrites the secret under the account's
existing secrets_manager_arn, so the account keeps its same id,
default_language, and any posts already associated with it.

Usage (run inside the API container):
    docker compose exec api python app/scripts/update_telegram_secret.py \
        --name "TV9 Hindi Telegram" \
        --bot-token "CORRECT_TOKEN" \
        --chat-id "CORRECT_CHAT_ID"
"""

import argparse

from app.database import SessionLocal
from app import models
from app.services import secrets_service


def main():
    parser = argparse.ArgumentParser(description="Update an existing Telegram account's credentials")
    parser.add_argument("--name", required=True, help="Exact account_name as it shows in the dashboard")
    parser.add_argument("--bot-token", required=True, help="Corrected bot token from @BotFather")
    parser.add_argument("--chat-id", required=True, help="Corrected chat_id")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        account = (
            db.query(models.SocialAccount)
            .filter(
                models.SocialAccount.platform == "telegram",
                models.SocialAccount.account_name == args.name,
            )
            .first()
        )
        if not account:
            print(f"No Telegram account found named '{args.name}'. Check the exact name in the dashboard.")
            return

        secrets_service.save_secret(
            account.secrets_manager_arn,
            {
                "bot_token": args.bot_token,
                "chat_id": args.chat_id,
            },
        )
        print(f"Updated credentials for '{account.account_name}' (id={account.id}) — no new account created.")
    finally:
        db.close()


if __name__ == "__main__":
    main()