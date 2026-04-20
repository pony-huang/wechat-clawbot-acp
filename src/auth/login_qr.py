"""QR code login flow for Weixin (mirrors TypeScript login-qr.ts)."""
import asyncio
import json
import secrets
import time

from src.api.client import api_get_fetch
from src.util.logger import logger
from src.util.qr_render import qr_code_to_terminal
from src.util.redact import redact_token

DEFAULT_ILINK_BOT_TYPE = "3"
FIXED_BASE_URL = "https://ilinkai.weixin.qq.com"
QR_LONG_POLL_TIMEOUT_MS = 35_000
ACTIVE_LOGIN_TTL_MS = 5 * 60_000
MAX_QR_REFRESH_COUNT = 3


class ActiveLogin:
    """In-memory record of an active QR login session."""

    __slots__ = (
        "session_key",
        "id",
        "qrcode",
        "qrcode_url",
        "started_at",
        "bot_token",
        "status",
        "error",
        "current_api_base_url",
    )

    def __init__(
        self,
        session_key: str,
        id: str,
        qrcode: str,
        qrcode_url: str,
        started_at: float,
    ) -> None:
        self.session_key = session_key
        self.id = id
        self.qrcode = qrcode
        self.qrcode_url = qrcode_url
        self.started_at = started_at
        self.bot_token: str | None = None
        self.status: str = "wait"
        self.error: str | None = None
        self.current_api_base_url: str | None = None


_active_logins: dict[str, ActiveLogin] = {}


def _is_login_fresh(login: ActiveLogin) -> bool:
    return (time.time() * 1000) - (login.started_at * 1000) < ACTIVE_LOGIN_TTL_MS


def _purge_expired_logins() -> None:
    now = time.time() * 1000
    for key, login in list(_active_logins.items()):
        if now - login.started_at * 1000 >= ACTIVE_LOGIN_TTL_MS:
            del _active_logins[key]


async def get_qr_code_ascii(qrcode_url: str, scale: int = 2) -> str | None:
    """Convert QR code URL/content to ASCII art for terminal display.

    Args:
        qrcode_url: The qrcode_img_content from the API (data URL or image URL)
        scale: Scale factor for the ASCII output

    Returns:
        ASCII art string, or None if conversion fails
    """
    return await qr_code_to_terminal(qrcode_url, scale)


async def _fetch_qr_code(api_base_url: str, bot_type: str) -> dict:
    """Fetch QR code from the Weixin ilink API."""
    logger.info(f"Fetching QR code from: {api_base_url} bot_type={bot_type}")
    raw_text = await api_get_fetch(
        base_url=api_base_url,
        endpoint=f"ilink/bot/get_bot_qrcode?bot_type={bot_type}",
        label="fetchQRCode",
    )
    return json.loads(raw_text)  # type: ignore


async def _poll_qr_status(api_base_url: str, qrcode: str) -> dict:
    """Long-poll QR code scan status."""
    try:
        raw_text = await api_get_fetch(
            base_url=api_base_url,
            endpoint=f"ilink/bot/get_qrcode_status?qrcode={qrcode}",
            timeout_ms=QR_LONG_POLL_TIMEOUT_MS,
            label="pollQRStatus",
        )
        logger.debug(f"pollQRStatus: body={raw_text[:200]}")
        return json.loads(raw_text)  # type: ignore
    except asyncio.TimeoutError:
        logger.debug(f"pollQRStatus: client-side timeout after {QR_LONG_POLL_TIMEOUT_MS}ms, returning wait")
        return {"status": "wait"}
    except Exception as err:
        logger.warn(f"pollQRStatus: network/gateway error, will retry: {err}")
        return {"status": "wait"}


async def start_weixin_login_with_qr(
    account_id: str | None = None,
    api_base_url: str = FIXED_BASE_URL,
    bot_type: str = DEFAULT_ILINK_BOT_TYPE,
    force: bool = False,
    timeout_ms: int | None = None,
    verbose: bool = False,
) -> dict:
    """Start QR login flow: fetch QR code, store in active logins, return URL."""
    session_key = account_id or secrets.token_hex()

    _purge_expired_logins()

    existing = _active_logins.get(session_key)
    if not force and existing and _is_login_fresh(existing) and existing.qrcode_url:
        return {
            "qrcode_url": existing.qrcode_url,
            "message": "二维码已就绪，请使用微信扫描。",
            "session_key": session_key,
        }

    try:
        qr_response = await _fetch_qr_code(FIXED_BASE_URL, bot_type)
        logger.info(
            f"QR code received, qrcode={redact_token(qr_response.get('qrcode', ''))} "
            f"imgContentLen={len(qr_response.get('qrcode_img_content', '') or '')}"
        )

        login = ActiveLogin(
            session_key=session_key,
            id=secrets.token_hex(),
            qrcode=qr_response["qrcode"],
            qrcode_url=qr_response["qrcode_img_content"],
            started_at=time.time(),
        )
        _active_logins[session_key] = login

        return {
            "qrcode_url": qr_response["qrcode_img_content"],
            "message": "使用微信扫描以下二维码，以完成连接。",
            "session_key": session_key,
        }
    except Exception as err:
        logger.error(f"Failed to start Weixin login: {err}")
        return {
            "message": f"Failed to start login: {err}",
            "session_key": session_key,
        }


async def wait_for_weixin_login(
    session_key: str,
    api_base_url: str = FIXED_BASE_URL,
    bot_type: str = DEFAULT_ILINK_BOT_TYPE,
    timeout_ms: int = 480_000,
    verbose: bool = False,
) -> dict:
    """Wait for QR scan confirmation with auto-refresh on expiry."""
    active_login = _active_logins.get(session_key)

    if not active_login:
        logger.warn(f"waitForWeixinLogin: no active login sessionKey={session_key}")
        return {"connected": False, "message": "当前没有进行中的登录，请先发起登录。"}

    if not _is_login_fresh(active_login):
        logger.warn(f"waitForWeixinLogin: login QR expired sessionKey={session_key}")
        del _active_logins[session_key]
        return {"connected": False, "message": "二维码已过期，请重新生成。"}

    timeout_ms = max(timeout_ms, 1000)
    deadline = (time.time() * 1000) + timeout_ms
    scanned_printed = False
    qr_refresh_count = 1

    active_login.current_api_base_url = FIXED_BASE_URL
    logger.info("Starting to poll QR code status...")

    while (time.time() * 1000) < deadline:
        try:
            current_base_url = active_login.current_api_base_url or FIXED_BASE_URL
            status_response = await _poll_qr_status(current_base_url, active_login.qrcode)
            logger.debug(
                f"pollQRStatus: status={status_response.get('status')} "
                f"hasBotToken={bool(status_response.get('bot_token'))}"
            )
            active_login.status = status_response.get("status", "wait")

            status = active_login.status

            if status == "wait":
                if verbose:
                    print(".", end="", flush=True)

            elif status == "scaned":
                if not scanned_printed:
                    print("\n👀 已扫码，在微信继续操作...\n", flush=True)
                    scanned_printed = True

            elif status == "expired":
                qr_refresh_count += 1
                if qr_refresh_count > MAX_QR_REFRESH_COUNT:
                    logger.warn(
                        f"waitForWeixinLogin: QR expired {MAX_QR_REFRESH_COUNT} times, giving up"
                    )
                    del _active_logins[session_key]
                    return {"connected": False, "message": "登录超时：二维码多次过期，请重新开始登录流程。"}

                print(f"\n⏳ 二维码已过期，正在刷新...({qr_refresh_count}/{MAX_QR_REFRESH_COUNT})\n", flush=True)
                logger.info(f"waitForWeixinLogin: QR expired, refreshing ({qr_refresh_count}/{MAX_QR_REFRESH_COUNT})")

                try:
                    qr_response = await _fetch_qr_code(FIXED_BASE_URL, bot_type)
                    active_login.qrcode = qr_response["qrcode"]
                    active_login.qrcode_url = qr_response["qrcode_img_content"]
                    active_login.started_at = time.time()
                    scanned_printed = False
                    logger.info(f"waitForWeixinLogin: new QR code obtained")
                    print("🔄 新二维码已生成，请重新扫描\n\n", flush=True)
                except Exception as refresh_err:
                    logger.error(f"waitForWeixinLogin: failed to refresh QR code: {refresh_err}")
                    del _active_logins[session_key]
                    return {"connected": False, "message": f"刷新二维码失败: {refresh_err}"}

            elif status == "scaned_but_redirect":
                redirect_host = status_response.get("redirect_host")
                if redirect_host:
                    new_base_url = f"https://{redirect_host}"
                    active_login.current_api_base_url = new_base_url
                    logger.info(f"waitForWeixinLogin: IDC redirect, switching to {redirect_host}")

            elif status == "confirmed":
                ilink_bot_id = status_response.get("ilink_bot_id")
                if not ilink_bot_id:
                    del _active_logins[session_key]
                    logger.error("Login confirmed but ilink_bot_id missing from response")
                    return {"connected": False, "message": "登录失败：服务器未返回 ilink_bot_id。"}

                active_login.bot_token = status_response.get("bot_token")
                del _active_logins[session_key]

                logger.info(
                    f"✅ Login confirmed! ilink_bot_id={ilink_bot_id} "
                    f"ilink_user_id={redact_token(status_response.get('ilink_user_id', ''))}"
                )

                return {
                    "connected": True,
                    "bot_token": status_response.get("bot_token"),
                    "account_id": ilink_bot_id,
                    "base_url": status_response.get("baseurl"),
                    "user_id": status_response.get("ilink_user_id"),
                    "message": "✅ 与微信连接成功！",
                }

        except Exception as err:
            logger.error(f"Error polling QR status: {err}")
            del _active_logins[session_key]
            return {"connected": False, "message": f"Login failed: {err}"}

        await asyncio.sleep(1)

    logger.warn(f"waitForWeixinLogin: timed out waiting for QR scan sessionKey={session_key}")
    del _active_logins[session_key]
    return {"connected": False, "message": "登录超时，请重试。"}
