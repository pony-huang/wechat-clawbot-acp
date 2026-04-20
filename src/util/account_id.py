"""Account ID normalization utilities.

This module provides functions for normalizing WeChat account IDs
to a canonical format.
"""

WEIXIN_ACCOUNT_SUFFIX = "@im.wechat"


def normalize_account_id_wechat(account_id: str) -> str:
    """Normalize a WeChat account ID to canonical format.

    Canonical format adds the @im.wechat suffix if not present.

    Args:
        account_id: Raw account ID from API or user input

    Returns:
        Normalized account ID with @im.wechat suffix

    Examples:
        >>> normalize_account_id_wechat("someaccountid")
        'someaccountid@im.wechat'
        >>> normalize_account_id_wechat("someaccountid@im.wechat")
        'someaccountid@im.wechat'
        >>> normalize_account_id_wechat("")
        ''
    """
    if not account_id:
        return ""

    # Already normalized
    if account_id.endswith(WEIXIN_ACCOUNT_SUFFIX):
        return account_id

    # Add suffix
    return account_id + WEIXIN_ACCOUNT_SUFFIX


def is_account_id_normalized(account_id: str) -> bool:
    """Check if an account ID is already normalized.

    Args:
        account_id: Account ID to check

    Returns:
        True if normalized, False otherwise
    """
    if not account_id:
        return True
    return account_id.endswith(WEIXIN_ACCOUNT_SUFFIX)


def extract_raw_account_id(account_id: str) -> str:
    """Extract the raw account ID without suffix.

    Args:
        account_id: Normalized or raw account ID

    Returns:
        Raw account ID without @im.wechat suffix

    Examples:
        >>> extract_raw_account_id("someaccountid@im.wechat")
        'someaccountid'
        >>> extract_raw_account_id("someaccountid")
        'someaccountid'
    """
    if not account_id:
        return ""

    if account_id.endswith(WEIXIN_ACCOUNT_SUFFIX):
        return account_id[: -len(WEIXIN_ACCOUNT_SUFFIX)]
    return account_id
