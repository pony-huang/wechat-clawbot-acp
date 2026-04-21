"""Weixin API HTTP client wrappers.

Mirrors the TypeScript api.ts functionality.
"""
import asyncio
import base64
import json
import os
import secrets
import tomllib
from typing import Any

import aiohttp

from src.api import types as t
from src.api.types import GetUpdatesReq, GetUpdatesResp
from src.util.logger import logger
from src.util.redact import redact_body, redact_token, redact_url


# ---------------------------------------------------------------------------
# Version / app ID helpers
# ---------------------------------------------------------------------------
def _read_project_metadata() -> tuple[str, str]:
    """Read channel version and iLink app ID from pyproject metadata."""
    project_root = os.path.join(os.path.dirname(__file__), "..", "..")
    pyproject_path = os.path.join(project_root, "pyproject.toml")
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return "0.0.0", ""

    project = data.get("project", {})
    tool_cfg = data.get("tool", {}).get("wechat_clawbot_acp", {})
    version = str(project.get("version", "0.0.0") or "0.0.0")
    ilink_appid = str(tool_cfg.get("ilink_appid", "") or "")
    return version, ilink_appid


CHANNEL_VERSION, ILINK_APP_ID = _read_project_metadata()


def _build_client_version(version: str) -> int:
    """Build iLink-App-ClientVersion: 0x00MMNNPP."""
    parts = version.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


ILINK_APP_CLIENT_VERSION: int = _build_client_version(CHANNEL_VERSION)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_API_TIMEOUT_MS = 15_000
DEFAULT_CONFIG_TIMEOUT_MS = 10_000


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else f"{url}/"


def _random_wechat_uin() -> str:
    """X-WECHAT-UIN header: random uint32 -> decimal string -> base64."""
    uint32_bytes = secrets.token_bytes(4)
    uint32 = int.from_bytes(uint32_bytes, "big")
    return base64.b64encode(str(uint32).encode()).decode()


# ---------------------------------------------------------------------------
# RouteTag loading
# ---------------------------------------------------------------------------
_route_tag: str | None = None


def _load_route_tag() -> str | None:
    """Load routeTag from wechat-acp.json (cached)."""
    global _route_tag
    if _route_tag is not None:
        return _route_tag

    try:
        state_dir = os.environ.get("WECHATACP_STATE_DIR", "").strip() or os.path.join(os.path.expanduser("~"), ".wechat-acp")
        config_path = os.path.join(state_dir, "wechat-acp.json")
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            channels = cfg.get("channels", {})
            section = channels.get("wechat-acp", {})
            route_tag = section.get("routeTag")
            if route_tag is not None:
                _route_tag = str(route_tag)
                return _route_tag
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
async def api_get_fetch(
    base_url: str,
    endpoint: str,
    timeout_ms: int | None = None,
    label: str = "",
) -> str:
    """GET fetch wrapper for Weixin API endpoints."""
    base = _ensure_trailing_slash(base_url)
    url = f"{base}{endpoint.lstrip('/')}"
    hdrs: dict[str, str] = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    route_tag = _load_route_tag()
    if route_tag:
        hdrs["SKRouteTag"] = route_tag

    logger.debug(f"GET {redact_url(url)}")

    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000 if timeout_ms else None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=hdrs) as res:
            raw_text = await res.text()
            logger.debug(f"{label} status={res.status} raw={redact_body(raw_text[:200])}")
            if not res.ok:
                raise RuntimeError(f"{label} {res.status}: {raw_text}")
            return raw_text


async def api_post_fetch(
    base_url: str,
    endpoint: str,
    body: str,
    token: str | None,
    timeout_ms: int,
    label: str,
) -> str:
    """POST JSON fetch wrapper for Weixin API endpoints."""
    base = _ensure_trailing_slash(base_url)
    url = f"{base}{endpoint.lstrip('/')}"

    content_len = len(body.encode("utf-8"))
    hdrs: dict[str, Any] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(content_len),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    route_tag = _load_route_tag()
    if route_tag:
        hdrs["SKRouteTag"] = route_tag
    if token and token.strip():
        hdrs["Authorization"] = f"Bearer {token.strip()}"

    logger.debug(f"POST {redact_url(url)} body={redact_body(body)}")

    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=body.encode("utf-8"), headers=hdrs) as res:
            raw_text = await res.text()
            logger.debug(f"{label} status={res.status} raw={redact_body(raw_text[:200])}")
            if not res.ok:
                raise RuntimeError(f"{label} {res.status}: {raw_text}")
            return raw_text


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------
async def get_updates(
    base_url: str,
    token: str | None,
    get_updates_buf: str = "",
    timeout_ms: int | None = None,
) -> t.GetUpdatesResp:
    """Long-poll getUpdates. Server holds request until new messages or timeout."""
    timeout = timeout_ms or DEFAULT_LONG_POLL_TIMEOUT_MS
    body = json.dumps({"get_updates_buf": get_updates_buf or "", "base_info": {"channel_version": CHANNEL_VERSION}})
    try:
        raw_text = await api_post_fetch(
            base_url=base_url,
            endpoint="ilink/bot/getupdates",
            body=body,
            token=token,
            timeout_ms=timeout,
            label="getUpdates",
        )
        return t.GetUpdatesResp.model_validate_json(raw_text)
    except asyncio.TimeoutError:
        logger.info(f"getUpdates: client-side timeout after {timeout}ms, returning empty response")
        return t.GetUpdatesResp(ret=0, msgs=[], get_updates_buf=get_updates_buf)


async def send_message(
    base_url: str,
    token: str | None,
    body: t.SendMessageReq,
    timeout_ms: int | None = None,
) -> None:
    """Send a single message downstream."""
    body_dict = body.model_dump(mode="json", exclude_none=True)
    body_dict["base_info"] = {"channel_version": CHANNEL_VERSION}
    body_str = json.dumps(body_dict)
    await api_post_fetch(
        base_url=base_url,
        endpoint="ilink/bot/sendmessage",
        body=body_str,
        token=token,
        timeout_ms=timeout_ms or DEFAULT_API_TIMEOUT_MS,
        label="sendMessage",
    )


async def get_upload_url(
    base_url: str,
    token: str | None,
    filekey: str,
    media_type: int,
    to_user_id: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey: str,
    thumb_rawsize: int | None = None,
    thumb_rawfilemd5: str | None = None,
    thumb_filesize: int | None = None,
    no_need_thumb: bool | None = None,
    timeout_ms: int | None = None,
) -> t.GetUploadUrlResp:
    """Get pre-signed CDN upload URL."""
    payload: dict[str, Any] = {
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "aeskey": aeskey,
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    if thumb_rawsize is not None:
        payload["thumb_rawsize"] = thumb_rawsize
    if thumb_rawfilemd5 is not None:
        payload["thumb_rawfilemd5"] = thumb_rawfilemd5
    if thumb_filesize is not None:
        payload["thumb_filesize"] = thumb_filesize
    if no_need_thumb is not None:
        payload["no_need_thumb"] = no_need_thumb

    raw_text = await api_post_fetch(
        base_url=base_url,
        endpoint="ilink/bot/getuploadurl",
        body=json.dumps(payload),
        token=token,
        timeout_ms=timeout_ms or DEFAULT_API_TIMEOUT_MS,
        label="getUploadUrl",
    )
    return t.GetUploadUrlResp.model_validate_json(raw_text)


async def get_config(
    base_url: str,
    token: str | None,
    ilink_user_id: str,
    context_token: str | None = None,
    timeout_ms: int | None = None,
) -> t.GetConfigResp:
    """Fetch bot config (includes typing_ticket) for a given user."""
    payload: dict[str, Any] = {
        "ilink_user_id": ilink_user_id,
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    if context_token:
        payload["context_token"] = context_token

    raw_text = await api_post_fetch(
        base_url=base_url,
        endpoint="ilink/bot/getconfig",
        body=json.dumps(payload),
        token=token,
        timeout_ms=timeout_ms or DEFAULT_CONFIG_TIMEOUT_MS,
        label="getConfig",
    )
    return t.GetConfigResp.model_validate_json(raw_text)


async def send_typing(
    base_url: str,
    token: str | None,
    ilink_user_id: str,
    typing_ticket: str,
    status: int,
    timeout_ms: int | None = None,
) -> None:
    """Send typing indicator to a user."""
    payload: dict[str, Any] = {
        "ilink_user_id": ilink_user_id,
        "typing_ticket": typing_ticket,
        "status": status,
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    await api_post_fetch(
        base_url=base_url,
        endpoint="ilink/bot/sendtyping",
        body=json.dumps(payload),
        token=token,
        timeout_ms=timeout_ms or DEFAULT_CONFIG_TIMEOUT_MS,
        label="sendTyping",
    )
