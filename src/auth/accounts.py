"""Weixin account storage (JSON files, mirroring TypeScript accounts.ts)."""
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from src.storage.state_dir import resolve_state_dir
from src.util.logger import logger

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


# ---------------------------------------------------------------------------
# Account ID normalization
# ---------------------------------------------------------------------------
def normalize_account_id_filesystem(raw: str) -> str:
    """Normalize Weixin account ID to filesystem-safe form.

    e.g. "hex@im.bot" → "hex-im-bot", "hex@im.wechat" → "hex-im-wechat"
    """
    return raw.replace("@", "-").replace(".", "-")


def derive_raw_account_id(normalized_id: str) -> str | None:
    """Reverse normalize for compatibility: "hex-im-bot" → "hex@im.bot"."""
    if normalized_id.endswith("-im-bot"):
        return f"{normalized_id[:-7]}@im.bot"
    if normalized_id.endswith("-im-wechat"):
        return f"{normalized_id[:-10]}@im.wechat"
    return None


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------
def _resolve_weixin_state_dir() -> str:
    return os.path.join(resolve_state_dir(), "wechat-acp")


def _resolve_accounts_dir() -> str:
    return os.path.join(_resolve_weixin_state_dir(), "accounts")


def _resolve_account_index_path() -> str:
    return os.path.join(_resolve_weixin_state_dir(), "accounts.json")


# ---------------------------------------------------------------------------
# Account index (persistent list of registered account IDs)
# ---------------------------------------------------------------------------
def list_indexed_weixin_account_ids() -> list[str]:
    """Returns all accountIds registered via QR login."""
    file_path = _resolve_account_index_path()
    try:
        if not os.path.exists(file_path):
            return []
        with open(file_path, encoding="utf-8") as f:
            parsed = json.load(f)
        if not isinstance(parsed, list):
            return []
        return [id for id in parsed if isinstance(id, str) and id.strip()]
    except Exception:
        return []


def register_weixin_account_id(account_id: str) -> None:
    """Add accountId to the persistent index (no-op if already present)."""
    dir_path = _resolve_weixin_state_dir()
    os.makedirs(dir_path, exist_ok=True)

    existing = list_indexed_weixin_account_ids()
    if account_id in existing:
        return

    updated = existing + [account_id]
    with open(_resolve_account_index_path(), "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)


def unregister_weixin_account_id(account_id: str) -> None:
    """Remove accountId from the persistent index."""
    existing = list_indexed_weixin_account_ids()
    updated = [id for id in existing if id != account_id]
    if len(updated) != len(existing):
        with open(_resolve_account_index_path(), "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=2)


def clear_stale_accounts_for_user_id(
    current_account_id: str,
    user_id: str,
    on_clear_context_tokens: "callable | None" = None,
) -> None:
    """Remove stale accounts that share the same userId as the newly-bound account."""
    if not user_id:
        return
    all_ids = list_indexed_weixin_account_ids()
    for id_ in all_ids:
        if id_ == current_account_id:
            continue
        data = load_weixin_account(id_)
        if data and data.get("userId", "").strip() == user_id:
            logger.info(f"clearStaleAccountsForUserId: removing stale account={id_} (same userId={user_id})")
            if on_clear_context_tokens:
                on_clear_context_tokens(id_)
            clear_weixin_account(id_)
            unregister_weixin_account_id(id_)


# ---------------------------------------------------------------------------
# Account data types
# ---------------------------------------------------------------------------
class WeixinAccountData:
    """Per-account data: token + baseUrl in one file."""

    __slots__ = ("token", "saved_at", "base_url", "user_id")

    def __init__(
        self,
        token: str | None = None,
        saved_at: str | None = None,
        base_url: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.token = token
        self.saved_at = saved_at
        self.base_url = base_url
        self.user_id = user_id


# ---------------------------------------------------------------------------
# Account store (per-account credential files)
# ---------------------------------------------------------------------------
def _resolve_account_path(account_id: str) -> str:
    return os.path.join(_resolve_accounts_dir(), f"{account_id}.json")


def _load_legacy_token() -> str | None:
    """Load token from legacy single-file credentials path."""
    legacy_path = os.path.join(
        resolve_state_dir(), "credentials", "wechat-acp", "credentials.json"
    )
    try:
        if not os.path.exists(legacy_path):
            return None
        with open(legacy_path, encoding="utf-8") as f:
            parsed = json.load(f)
        if isinstance(parsed, dict) and isinstance(parsed.get("token"), str):
            return parsed["token"]
    except Exception:
        pass
    return None


def _read_account_file(file_path: str) -> dict | None:
    try:
        if os.path.exists(file_path):
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def load_weixin_account(account_id: str) -> dict | None:
    """Load account data by ID, with compatibility fallbacks."""
    # Primary: try given accountId (normalized IDs)
    primary = _read_account_file(_resolve_account_path(account_id))
    if primary:
        return primary

    # Compat: if given ID is normalized, derive old raw filename
    raw_id = derive_raw_account_id(account_id)
    if raw_id:
        compat = _read_account_file(_resolve_account_path(raw_id))
        if compat:
            return compat

    # Legacy fallback: read token from old single-account credentials file
    token = _load_legacy_token()
    if token:
        return {"token": token}

    return None


def save_weixin_account(
    account_id: str,
    update: dict,
) -> None:
    """Persist account data after QR login (merges into existing file)."""
    dir_path = _resolve_accounts_dir()
    os.makedirs(dir_path, exist_ok=True)

    existing = load_weixin_account(account_id) or {}

    token = update.get("token", "").strip() or existing.get("token", "").strip() or None
    if token:
        existing["token"] = token
        existing["savedAt"] = __import__("datetime").datetime.utcnow().isoformat()
    if update.get("base_url", "").strip():
        existing["baseUrl"] = update["base_url"].strip()
    user_id = update.get("user_id")
    if user_id is not None:
        existing["userId"] = user_id.strip() or None

    file_path = _resolve_account_path(account_id)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    try:
        os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass  # best-effort


def clear_weixin_account(account_id: str) -> None:
    """Remove all files associated with an account."""
    dir_path = _resolve_accounts_dir()
    for fname in [f"{account_id}.json", f"{account_id}.sync.json", f"{account_id}.context-tokens.json"]:
        try:
            path = os.path.join(dir_path, fname)
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
    # Also clear allowFrom
    try:
        from src.auth.pairing import resolve_framework_allow_from_path

        os.unlink(resolve_framework_allow_from_path(account_id))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# RouteTag loading from wechat-acp.json
# ---------------------------------------------------------------------------
_cached_route_tag_section: dict | None = None


def _load_route_tag_section() -> dict | None:
    """Load the wechat-acp section from wechat-acp.json (cached)."""
    global _cached_route_tag_section
    if _cached_route_tag_section is not None:
        return _cached_route_tag_section

    try:
        config_path = os.path.join(resolve_state_dir(), "wechat-acp.json")
        if not os.path.exists(config_path):
            _cached_route_tag_section = None
            return None
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        channels = cfg.get("channels", {})
        section = channels.get("wechat-acp")
        if isinstance(section, dict):
            _cached_route_tag_section = section
            return section
    except Exception:
        pass

    _cached_route_tag_section = None
    return None


def load_config_route_tag(account_id: str | None = None) -> str | None:
    """Load SKRouteTag from wechat-acp.json per-account or section level."""
    section = _load_route_tag_section()
    if not section:
        return None

    if account_id:
        accounts = section.get("accounts")
        if isinstance(accounts, dict):
            acct = accounts.get(account_id)
            if isinstance(acct, dict):
                tag = acct.get("routeTag")
                if isinstance(tag, (int, str)) and str(tag).strip():
                    return str(tag).strip()

    tag = section.get("routeTag")
    if isinstance(tag, (int, str)) and str(tag).strip():
        return str(tag).strip()
    return None


# ---------------------------------------------------------------------------
# Resolved account type
# ---------------------------------------------------------------------------
@dataclass
class ResolvedWeixinAccount:
    """Resolved account: merge config + stored credentials."""

    account_id: str
    base_url: str
    cdn_base_url: str
    token: str | None
    enabled: bool
    configured: bool
    name: str | None = None


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------
def list_weixin_account_ids(_cfg: dict) -> list[str]:
    """List accountIds from the index file."""
    return list_indexed_weixin_account_ids()


def resolve_weixin_account(cfg: dict, account_id: str | None) -> ResolvedWeixinAccount:
    """Resolve a Weixin account by ID, merging config and stored credentials."""
    if not account_id or not account_id.strip():
        raise RuntimeError("weixin: accountId is required (no default account)")

    raw = account_id.strip()
    id_ = normalize_account_id_filesystem(raw)
    section = cfg.get("channels", {}).get("wechat-acp", {})
    account_cfg: dict = {}
    if isinstance(section, dict):
        accounts = section.get("accounts")
        if isinstance(accounts, dict):
            account_cfg = accounts.get(id_, section)
        else:
            account_cfg = section

    account_data = load_weixin_account(id_)
    token = account_data.get("token", "").strip() if account_data else ""
    state_base_url = account_data.get("baseUrl", "").strip() if account_data else ""

    return ResolvedWeixinAccount(
        account_id=id_,
        base_url=state_base_url or DEFAULT_BASE_URL,
        cdn_base_url=account_cfg.get("cdnBaseUrl", "").strip() or CDN_BASE_URL,
        token=token or None,
        enabled=account_cfg.get("enabled", True) is not False,
        configured=bool(token),
        name=account_cfg.get("name", "").strip() or None,
    )
