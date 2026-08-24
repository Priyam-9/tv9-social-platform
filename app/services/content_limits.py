"""
Per-platform content limits, checked before anything is sent to a
platform's API. These figures should be re-verified against each
platform's live documentation periodically, since platforms change
them without much notice — treat this as "best known as of the last
check", not a permanent guarantee.
"""

PLATFORM_LIMITS = {
    "youtube": {"title": 100, "caption": 5000},
    "instagram": {"caption": 2200},
    "facebook": {"caption": 63206},
    "x": {"caption": 280},
    # Telegram's sendMessage text limit is 4096 characters. Our
    # TelegramAdapter composes title + caption + link into one message,
    # so this checks caption alone as an approximation — the adapter's
    # actual combined text could still exceed 4096 in an edge case
    # (very long title + very long caption + link all at once). Worth
    # tightening later if that turns out to matter in practice.
    "telegram": {"caption": 4096},
}


def validate_content(platform: str, title: str | None, caption: str | None) -> list[str]:
    """
    Returns a list of human-readable violation messages — empty if the
    content is within that platform's limits. A single post's title and
    caption are shared across every target platform, so the SAME
    content might be fine for YouTube but too long for X — this is
    checked per-platform, not once globally.
    """
    limits = PLATFORM_LIMITS.get(platform, {})
    violations = []

    if "title" in limits and title and len(title) > limits["title"]:
        violations.append(
            f"Title is {len(title)} characters, exceeds {platform}'s {limits['title']}-character limit"
        )

    if "caption" in limits and caption and len(caption) > limits["caption"]:
        violations.append(
            f"Caption is {len(caption)} characters, exceeds {platform}'s {limits['caption']}-character limit"
        )

    return violations