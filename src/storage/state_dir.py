"""Storage directory resolution.

Resolves the WeChat-ACP state directory (mirrors core logic in src/infra).
"""
import os
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / ".wechat-acp"


def resolve_state_dir() -> str:
    """Resolve the WeChat-ACP state directory.

    Priority:
    1. WECHATACP_STATE_DIR env var
    2. WECHATACP_STATE_DIR env var
    3. ~/.wechat-acp
    """
    return (
        os.environ.get("WECHATACP_STATE_DIR", "").strip()
        or os.environ.get("WECHATACP_STATE_DIR", "").strip()
        or str(DEFAULT_STATE_DIR)
    )


def resolve_preferred_wechatacp_tmp_dir() -> str:
    """Resolve the preferred temporary directory for WeChat-ACP runtime files.

    Priority:
    1. WECHATACP_TMP_DIR env var
    2. ~/.wechat-acp/tmp

    Creates the directory if it doesn't exist.
    """
    wechatacp_tmp = os.environ.get("WECHATACP_TMP_DIR", "").strip()
    if not wechatacp_tmp:
        wechatacp_tmp = os.path.join(resolve_state_dir(), "tmp")

    # Create directory if it doesn't exist
    Path(wechatacp_tmp).mkdir(parents=True, exist_ok=True)
    return wechatacp_tmp
