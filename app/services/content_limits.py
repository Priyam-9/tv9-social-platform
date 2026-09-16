"""
Per-platform content limits and shared language validation.

Content limits are checked before anything is sent to a platform's API.
These figures should be re-verified against each platform's live
documentation periodically, since platforms can change them without much
notice.

Language validation is intentionally centralized here so the backend and
frontend can use one supported-language vocabulary rather than accepting
arbitrary language strings.
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


# Supported language codes used by the publisher.
#
# Keep this list intentionally small and explicit. The current dashboard
# supports these language choices and the codes are stored in
# SocialAccount.default_language / PostTarget.language.
SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "bn": "Bengali",
    "mr": "Marathi",
    "ta": "Tamil",
    "kn": "Kannada",
    "ml": "Malayalam",
}


def normalize_language(language: str | None) -> str | None:
    """
    Normalize a language code for storage/comparison.

    Returns None for an empty value. Supported values are lower-cased and
    surrounding whitespace is removed.
    """
    if language is None:
        return None

    normalized = language.strip().lower()

    if not normalized:
        return None

    return normalized


def validate_language(language: str | None) -> list[str]:
    """
    Validate a language code.

    Returns an empty list when the value is absent or supported, otherwise
    returns one human-readable violation message.
    """
    normalized = normalize_language(language)

    if normalized is None:
        return []

    if normalized not in SUPPORTED_LANGUAGES:
        supported = ", ".join(SUPPORTED_LANGUAGES.keys())
        return [
            f"Unsupported language code '{normalized}'. "
            f"Supported languages: {supported}"
        ]

    return []


def validate_content(
    platform: str,
    title: str | None,
    caption: str | None,
) -> list[str]:
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
            f"Title is {len(title)} characters, exceeds "
            f"{platform}'s {limits['title']}-character limit"
        )

    if "caption" in limits and caption and len(caption) > limits["caption"]:
        violations.append(
            f"Caption is {len(caption)} characters, exceeds "
            f"{platform}'s {limits['caption']}-character limit"
        )

    return violations
