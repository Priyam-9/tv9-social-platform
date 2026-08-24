from app.adapters.base import PlatformAdapter
from app.adapters.youtube_adapter import YouTubeAdapter
from app.adapters.telegram_adapter import TelegramAdapter

# As Instagram/Facebook/X adapters get built, register them here too.
ADAPTERS: dict[str, PlatformAdapter] = {
    "youtube": YouTubeAdapter(),
    "telegram": TelegramAdapter(),
}


def get_adapter(platform: str) -> PlatformAdapter:
    adapter = ADAPTERS.get(platform)
    if adapter is None:
        raise ValueError(f"No adapter registered for platform: {platform}")
    return adapter