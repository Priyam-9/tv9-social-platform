"""
One-time setup script to register a Telegram channel/group as a
SocialAccount. Telegram has no OAuth login flow like YouTube's
/auth/youtube/login — a bot token + chat_id is all that's needed, and
there's no browser-based flow to capture it automatically, so this
has to be run manually once per channel.

How to get a bot token and chat_id:
1. Message @BotFather on Telegram, send /newbot, follow the prompts.
   You'll get back a token that looks like: 123456789:ABCdefGhIJKl...
2. Add that bot as an ADMIN to your target channel or group — it can't
   post without admin rights there.
3. Post any message in that channel, then visit (in a browser):
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   Look for "chat":{"id": ...} in the response — that number
   (often negative for channels/groups, e.g. -1001234567890) is your
   chat_id.

Usage (run inside the API container):
    docker compose exec api python app/scripts/register_telegram_account.py \
        --name "TV9 Hindi Telegram" \
        --bot-token "123456789:ABCdefGhIJKl..." \
        --chat-id "-1001234567890" \
        --language hi
"""

import argparse
import uuid

from app.database import SessionLocal
from app import models
from app.services import secrets_service


def main():
    parser = argparse.ArgumentParser(description="Register a Telegram account for TV9 Publisher")
    parser.add_argument("--name", required=True, help="Display name, e.g. 'TV9 Hindi Telegram'")
    parser.add_argument("--bot-token", required=True, help="Bot token from @BotFather")
    parser.add_argument("--chat-id", required=True, help="Target channel/group chat_id")
    parser.add_argument("--language", default=None, help="Default language code, e.g. hi, mr (optional)")
    args = parser.parse_args()

    secret_key = f"telegram:{uuid.uuid4()}"
    arn = secrets_service.save_secret(
        secret_key,
        {
            "bot_token": args.bot_token,
            "chat_id": args.chat_id,
        },
    )

    db = SessionLocal()
    try:
        account = models.SocialAccount(
            platform="telegram",
            account_name=args.name,
            secrets_manager_arn=arn,
            default_language=args.language,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        print(f"Registered Telegram account: {account.account_name} (id={account.id})")
        print("It will now show up in the dashboard's target account list.")
    finally:
        db.close()


if __name__ == "__main__":
    main()