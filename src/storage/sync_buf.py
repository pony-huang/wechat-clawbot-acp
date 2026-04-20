"""get_updates_buf persistence (sync buffer)."""
import json
import os
from typing import Optional

from src.auth.accounts import derive_raw_account_id
from src.storage.state_dir import resolve_state_dir

# Resolve paths
_OPENCLAW_WEIXIN_DIR = os.path.join(resolve_state_dir(), "wechat-acp")
_ACCOUNTS_DIR = os.path.join(_OPENCLAW_WEIXIN_DIR, "accounts")

# Legacy paths
_LEGACY_SYNCBUF_DIR = os.path.join(resolve_state_dir(), "agents", "default", "sessions", ".wechat-acp-sync")


def _ensure_accounts_dir() -> None:
    os.makedirs(_ACCOUNTS_DIR, exist_ok=True)


def get_sync_buf_file_path(account_id: str) -> str:
    """Path to the persistent get_updates_buf file for an account.

    Stored as: ~/.wechat-acp/wechat-acp/accounts/{accountId}.sync.json
    """
    _ensure_accounts_dir()
    return os.path.join(_ACCOUNTS_DIR, f"{account_id}.sync.json")


def _read_sync_buf_file(file_path: str) -> Optional[str]:
    """Read a sync buf file, returning the get_updates_buf value or None."""
    try:
        if not os.path.exists(file_path):
            return None
        with open(file_path, encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("get_updates_buf"), str):
            return data["get_updates_buf"]
    except Exception:
        pass
    return None


def load_get_updates_buf(file_path: str) -> Optional[str]:
    """Load persisted get_updates_buf.

    Fallback order:
    1. Primary path (normalized accountId)
    2. Compat path (raw accountId derived from pattern)
    3. Legacy single-account path
    """
    value = _read_sync_buf_file(file_path)
    if value is not None:
        return value

    # Compat: try raw-ID filename
    account_id = os.path.basename(file_path).replace(".sync.json", "")
    raw_id = derive_raw_account_id(account_id)
    if raw_id:
        compat_path = os.path.join(_ACCOUNTS_DIR, f"{raw_id}.sync.json")
        compat_value = _read_sync_buf_file(compat_path)
        if compat_value is not None:
            return compat_value

    # Legacy fallback
    legacy_path = os.path.join(_LEGACY_SYNCBUF_DIR, "default.json")
    return _read_sync_buf_file(legacy_path)


def save_get_updates_buf(file_path: str, get_updates_buf: str) -> None:
    """Persist get_updates_buf. Creates parent dir if needed."""
    dir_path = os.path.dirname(file_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump({"get_updates_buf": get_updates_buf}, f)
