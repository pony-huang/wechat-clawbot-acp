"""Redaction utilities for safe logging of sensitive fields."""
from urllib.parse import urlparse

DEFAULT_BODY_MAX_LEN = 200
DEFAULT_TOKEN_PREFIX_LEN = 6

SENSITIVE_FIELDS = frozenset({"context_token", "bot_token", "token", "authorization", "Authorization"})


def truncate(s: str | None, max_len: int) -> str:
    """Truncate a string, appending a length indicator when trimmed.

    Returns '' for empty/None input.
    """
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return f"{s[:max_len]}…(len={len(s)})"


def redact_token(token: str | None, prefix_len: int = DEFAULT_TOKEN_PREFIX_LEN) -> str:
    """Redact a token/secret: show only the first few chars + total length.

    Returns '(none)' when absent.
    """
    if not token:
        return "(none)"
    if len(token) <= prefix_len:
        return f"****(len={len(token)})"
    return f"{token[:prefix_len]}…(len={len(token)})"


def redact_body(body: str | None, max_len: int = DEFAULT_BODY_MAX_LEN) -> str:
    """Truncate a JSON body string for safe logging.

    Redacts known sensitive fields before truncating.
    """
    if not body:
        return "(empty)"
    # Mask values of known sensitive JSON keys: "key":"value" → "key":"<redacted>"
    import re
    redacted = re.sub(
        r'"(context_token|bot_token|token|authorization|Authorization)"\s*:\s*"[^"]*"',
        '"\1":"<redacted>"',
        body,
    )
    if len(redacted) <= max_len:
        return redacted
    return f"{redacted[:max_len]}…(truncated, totalLen={len(redacted)})"


def redact_url(raw_url: str) -> str:
    """Strip query string from URL for safe logging (often contains signatures/tokens)."""
    try:
        u = urlparse(raw_url)
        base = f"{u.scheme}://{u.netloc}{u.path}"
        return f"{base}?<redacted>" if u.query else base
    except Exception:
        return truncate(raw_url, 80)
