"""Framework allowFrom store (mirrors TypeScript pairing.ts).

Provides file-based allowFrom list management with proper file locking
to prevent race conditions during concurrent access.
"""
import json
import os
import sys
from dataclasses import dataclass

from src.storage.state_dir import resolve_state_dir
from src.util.logger import logger

# File lock support: fcntl on Unix, msvcrt on Windows
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


# ---------------------------------------------------------------------------
# File lock context manager
# ---------------------------------------------------------------------------
class _FileLock:
    """Cross-platform file lock using fcntl (Unix) or msvcrt (Windows)."""

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._lock_file: str = f"{file_path}.lock"
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        import os as _os

        lock_dir = os.path.dirname(self._lock_file)
        if lock_dir:
            _os.makedirs(lock_dir, exist_ok=True)
        self._fd = os.open(self._lock_file, os.O_CREAT | os.O_RDWR)
        if sys.platform == "win32":
            _lock_file(self._fd, msvcrt.LK_LOCK)  # type: ignore
        else:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args) -> None:
        if self._fd is not None:
            if sys.platform == "win32":
                _unlock_file(self._fd, msvcrt.LK_UNLCK)  # type: ignore
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


# Windows-specific lock functions that work with file descriptors
if sys.platform == "win32":

    def _lock_file(fd: int, lock_type: int) -> None:
        """Lock a region of a file using msvcrt (Windows)."""
        msvcrt.locking(fd, lock_type, 1)

    def _unlock_file(fd: int, lock_type: int) -> None:
        """Unlock a region of a file using msvcrt (Windows)."""
        msvcrt.locking(fd, lock_type, 1)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def _safe_key(raw: str) -> str:
    """Sanitize a channel/account key for safe use in filenames."""
    trimmed = raw.strip().lower()
    if not trimmed:
        raise ValueError("invalid key for allowFrom path")
    safe = trimmed.replace("/", "_").replace("\\", "_").replace(":", "_").replace("*", "_").replace("?", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_").replace("..", "_")
    if not safe or safe == "_":
        raise ValueError("invalid key for allowFrom path")
    return safe


def resolve_framework_allow_from_path(account_id: str) -> str:
    """Path to the framework allowFrom file for a given account.

    Path: <credDir>/wechat-acp-<accountId>-allowFrom.json
    """
    cred_dir = os.environ.get("WECHATACP_OAUTH_DIR", "").strip()
    if not cred_dir:
        cred_dir = os.path.join(resolve_state_dir(), "credentials")

    base = _safe_key("wechat-acp")
    safe_account = _safe_key(account_id)
    return os.path.join(cred_dir, f"{base}-{safe_account}-allowFrom.json")


# ---------------------------------------------------------------------------
# Read allowFrom list
# ---------------------------------------------------------------------------
def read_framework_allow_from_list(account_id: str) -> list[str]:
    """Read the framework allowFrom list for an account.

    Returns an empty array when the file is missing or unreadable.
    """
    file_path = resolve_framework_allow_from_path(account_id)
    try:
        if not os.path.exists(file_path):
            return []
        with open(file_path, encoding="utf-8") as f:
            parsed = json.load(f)
        allow_from = parsed.get("allowFrom") if isinstance(parsed, dict) else None
        if isinstance(allow_from, list):
            return [id_ for id_ in allow_from if isinstance(id_, str) and id_.strip()]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Write allowFrom list (with file lock)
# ---------------------------------------------------------------------------
async def register_user_in_framework_store(
    account_id: str,
    user_id: str,
) -> dict:
    """Register a user ID in the framework's channel allowFrom store.

    Uses file locking to prevent race conditions during concurrent access.
    """
    trimmed_user_id = user_id.strip()
    if not trimmed_user_id:
        return {"changed": False}

    file_path = resolve_framework_allow_from_path(account_id)
    dir_path = os.path.dirname(file_path)
    os.makedirs(dir_path, exist_ok=True)

    # Use file lock to prevent concurrent write conflicts
    with _FileLock(file_path):
        if not os.path.exists(file_path):
            initial = {"version": 1, "allowFrom": []}
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(initial, f, indent=2)

        content: dict = {"version": 1, "allowFrom": []}
        try:
            with open(file_path, encoding="utf-8") as f:
                parsed = json.load(f)
            if isinstance(parsed, dict) and isinstance(parsed.get("allowFrom"), list):
                content = parsed
        except Exception:
            pass

        if trimmed_user_id in content.get("allowFrom", []):
            return {"changed": False}

        content.setdefault("allowFrom", [])
        content["allowFrom"].append(trimmed_user_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)

    logger.info(
        f"registerUserInFrameworkStore: added userId={trimmed_user_id} accountId={account_id} path={file_path}"
    )
    return {"changed": True}


# ---------------------------------------------------------------------------
# Config write trigger (stub — requires wechat-acp Python SDK)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AllowFromPolicy:
    mode: str
    allow_list: tuple[str, ...]
    file_path: str


def resolve_allow_from_policy(account_id: str = "default") -> AllowFromPolicy:
    """Resolve the explicit single-account allowFrom policy for an account."""
    file_path = resolve_framework_allow_from_path(account_id)
    if not os.path.exists(file_path):
        return AllowFromPolicy(
            mode="unrestricted",
            allow_list=(),
            file_path=file_path,
        )
    return AllowFromPolicy(
        mode="allowlist",
        allow_list=tuple(read_framework_allow_from_list(account_id)),
        file_path=file_path,
    )


def is_user_allowed(user_id: str, account_id: str = "default") -> bool:
    """Check if a user is in the allowFrom list for an account.

    Single-account policy:
    - no allowFrom file => unrestricted mode, all users allowed
    - allowFrom file exists => allowlist mode, only listed users allowed

    Args:
        user_id: User ID to check
        account_id: Account ID to check against (default: "default")

    Returns:
        True if user is allowed, False otherwise
    """
    policy = resolve_allow_from_policy(account_id)
    if policy.mode == "unrestricted":
        return True
    return user_id.strip() in policy.allow_list


async def trigger_weixin_channel_reload() -> None:
    """Bump channelConfigUpdatedAt in wechat-acp.json so gateway reloads config.

    Writes a timestamp file that can be monitored for config changes.
    """
    try:
        from src.storage.state_dir import resolve_state_dir

        config_timestamp_path = os.path.join(resolve_state_dir(), "credentials", ".weixin_channel_config_updated")
        os.makedirs(os.path.dirname(config_timestamp_path), exist_ok=True)
        with open(config_timestamp_path, "w", encoding="utf-8") as f:
            import time

            f.write(str(int(time.time() * 1000)))
        logger.info(f"triggerWeixinChannelReload: config updated at {config_timestamp_path}")
    except Exception as err:
        logger.warn(f"triggerWeixinChannelReload: failed to update config timestamp: {err}")
