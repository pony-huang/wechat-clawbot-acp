"""Tests for QR login flow (auth/login_qr.py)."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.auth.login_qr import (
    DEFAULT_ILINK_BOT_TYPE,
    MAX_QR_REFRESH_COUNT,
    _active_logins,
    _fetch_qr_code,
    _is_login_fresh,
    _poll_qr_status,
    _purge_expired_logins,
    start_weixin_login_with_qr,
    wait_for_weixin_login,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clean_active_logins():
    """Clear active logins before and after each test."""
    _active_logins.clear()
    yield
    _active_logins.clear()


# ---------------------------------------------------------------------------
# Helper mocks
# ---------------------------------------------------------------------------
def mock_qr_response(qrcode="test-qrcode-12345", qrcode_img_content="data:image/png;base64,iVBORw0KGgo="):
    """Standard QR code API response."""
    return json.dumps({
        "qrcode": qrcode,
        "qrcode_img_content": qrcode_img_content,
    })


# ---------------------------------------------------------------------------
# _is_login_fresh
# ---------------------------------------------------------------------------
def test_is_login_fresh_valid():
    """Login within TTL should be fresh."""
    from src.auth.login_qr import ActiveLogin, ACTIVE_LOGIN_TTL_MS
    login = ActiveLogin(
        session_key="test",
        id="id",
        qrcode="qr",
        qrcode_url="url",
        started_at=(time.time() * 1000 - 60_000) / 1000,
    )
    assert _is_login_fresh(login) is True


def test_is_login_fresh_expired():
    """Login beyond TTL should not be fresh."""
    from src.auth.login_qr import ActiveLogin, ACTIVE_LOGIN_TTL_MS
    login = ActiveLogin(
        session_key="test",
        id="id",
        qrcode="qr",
        qrcode_url="url",
        started_at=(time.time() * 1000 - ACTIVE_LOGIN_TTL_MS - 1000) / 1000,
    )
    assert _is_login_fresh(login) is False


# ---------------------------------------------------------------------------
# _purge_expired_logins
# ---------------------------------------------------------------------------
def test_purge_expired_logins():
    """Should remove only expired logins, keep fresh ones."""
    from src.auth.login_qr import ActiveLogin, ACTIVE_LOGIN_TTL_MS

    fresh = ActiveLogin(
        session_key="fresh",
        id="id1",
        qrcode="qr1",
        qrcode_url="url1",
        started_at=time.time(),
    )
    expired = ActiveLogin(
        session_key="expired",
        id="id2",
        qrcode="qr2",
        qrcode_url="url2",
        started_at=(time.time() * 1000 - ACTIVE_LOGIN_TTL_MS - 1000) / 1000,
    )

    _active_logins["fresh"] = fresh
    _active_logins["expired"] = expired

    _purge_expired_logins()

    assert "fresh" in _active_logins
    assert "expired" not in _active_logins


# ---------------------------------------------------------------------------
# _fetch_qr_code
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_qr_code_success():
    """Should return parsed QR code response."""
    with patch("src.auth.login_qr.api_get_fetch", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_qr_response()
        resp = await _fetch_qr_code("https://ilinkai.weixin.qq.com", DEFAULT_ILINK_BOT_TYPE)

        assert resp["qrcode"] == "test-qrcode-12345"
        assert "qrcode_img_content" in resp
        mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_qr_code_api_error():
    """Should raise on API error."""
    with patch("src.auth.login_qr.api_get_fetch", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = RuntimeError("network error")
        with pytest.raises(RuntimeError):
            await _fetch_qr_code("https://ilinkai.weixin.qq.com", DEFAULT_ILINK_BOT_TYPE)


# ---------------------------------------------------------------------------
# _poll_qr_status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_poll_qr_status_wait_on_timeout():
    """Should return wait status on timeout."""
    with patch("src.auth.login_qr.api_get_fetch", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = asyncio.TimeoutError()
        resp = await _poll_qr_status("https://ilinkai.weixin.qq.com", "test-qrcode")
        assert resp["status"] == "wait"


@pytest.mark.asyncio
async def test_poll_qr_status_wait_on_network_error():
    """Should return wait status on network error."""
    with patch("src.auth.login_qr.api_get_fetch", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = RuntimeError("connection refused")
        resp = await _poll_qr_status("https://ilinkai.weixin.qq.com", "test-qrcode")
        assert resp["status"] == "wait"


@pytest.mark.asyncio
async def test_poll_qr_status_confirmed():
    """Should return confirmed status with bot token."""
    with patch("src.auth.login_qr.api_get_fetch", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = json.dumps({
            "status": "confirmed",
            "bot_token": "token-abc",
            "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": "user@im.wechat",
        })
        resp = await _poll_qr_status("https://ilinkai.weixin.qq.com", "test-qrcode")
        assert resp["status"] == "confirmed"
        assert resp["bot_token"] == "token-abc"
        assert resp["ilink_bot_id"] == "bot@im.bot"


# ---------------------------------------------------------------------------
# start_weixin_login_with_qr
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_login_fresh_returns_existing():
    """Should return existing QR if fresh and not forced."""
    from src.auth.login_qr import ActiveLogin

    existing = ActiveLogin(
        session_key="session-123",
        id="id",
        qrcode="existing-qr",
        qrcode_url="https://example.com/qr.png",
        started_at=time.time(),
    )
    _active_logins["session-123"] = existing

    result = await start_weixin_login_with_qr(account_id="session-123", force=False)

    assert result["qrcode_url"] == "https://example.com/qr.png"
    assert result["session_key"] == "session-123"
    assert "已就绪" in result["message"]


@pytest.mark.asyncio
async def test_start_login_force_refreshes():
    """Should fetch new QR when force=True."""
    with patch("src.auth.login_qr._fetch_qr_code", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"qrcode": "new-qr", "qrcode_img_content": "https://new.example.com/qr.png"}

        result = await start_weixin_login_with_qr(account_id="session-456", force=True)

        assert result["qrcode_url"] == "https://new.example.com/qr.png"
        assert "使用微信扫描" in result["message"]


@pytest.mark.asyncio
async def test_start_login_new_session():
    """Should fetch new QR for new session key."""
    with patch("src.auth.login_qr._fetch_qr_code", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"qrcode": "new-qr", "qrcode_img_content": "https://new.example.com/qr.png"}

        result = await start_weixin_login_with_qr()

        assert "session_key" in result
        assert result["qrcode_url"] == "https://new.example.com/qr.png"
        mock_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# wait_for_weixin_login
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wait_no_active_session():
    """Should return error when no active session."""
    result = await wait_for_weixin_login(session_key="nonexistent")

    assert result["connected"] is False
    assert "没有进行中的登录" in result["message"]


@pytest.mark.asyncio
async def test_wait_expired_session():
    """Should return error when session expired."""
    from src.auth.login_qr import ActiveLogin, ACTIVE_LOGIN_TTL_MS

    expired = ActiveLogin(
        session_key="expired-session",
        id="id",
        qrcode="qr",
        qrcode_url="url",
        started_at=(time.time() * 1000 - 6 * 60 * 1000) / 1000,
    )
    _active_logins["expired-session"] = expired

    result = await wait_for_weixin_login(session_key="expired-session")

    assert result["connected"] is False
    assert "过期" in result["message"]
    assert "expired-session" not in _active_logins


@pytest.mark.asyncio
async def test_wait_confirmed():
    """Should return connected=True on confirmed status."""
    from src.auth.login_qr import ActiveLogin

    login = ActiveLogin(
        session_key="confirmed-session",
        id="id",
        qrcode="confirmed-qr",
        qrcode_url="url",
        started_at=time.time(),
    )
    _active_logins["confirmed-session"] = login

    with patch("src.auth.login_qr._poll_qr_status", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {
            "status": "confirmed",
            "bot_token": "bot-token-xyz",
            "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": "user@im.wechat",
            "baseurl": "https://ilinkai.weixin.qq.com",
        }

        result = await wait_for_weixin_login(session_key="confirmed-session", timeout_ms=5000)

        assert result["connected"] is True
        assert result["bot_token"] == "bot-token-xyz"
        assert result["account_id"] == "bot@im.bot"
        assert result["user_id"] == "user@im.wechat"
        assert "confirmed-session" not in _active_logins


@pytest.mark.asyncio
async def test_wait_timeout():
    """Should return timeout error when polling expires."""
    from src.auth.login_qr import ActiveLogin

    login = ActiveLogin(
        session_key="timeout-session",
        id="id",
        qrcode="wait-qr",
        qrcode_url="url",
        started_at=time.time(),
    )
    _active_logins["timeout-session"] = login

    with patch("src.auth.login_qr._poll_qr_status", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {"status": "wait"}  # never changes

        result = await wait_for_weixin_login(session_key="timeout-session", timeout_ms=2000)

        assert result["connected"] is False
        assert "超时" in result["message"]


# ---------------------------------------------------------------------------
# Full flow: start + wait confirmed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_then_wait_confirmed():
    """Full flow: start QR → user scans → confirmed."""
    with patch("src.auth.login_qr._fetch_qr_code", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"qrcode": "full-qr", "qrcode_img_content": "https://example.com/qr.png"}

        start_result = await start_weixin_login_with_qr()
        assert start_result["qrcode_url"] == "https://example.com/qr.png"

        session_key = start_result["session_key"]

        with patch("src.auth.login_qr._poll_qr_status", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {
                "status": "confirmed",
                "bot_token": "final-token",
                "ilink_bot_id": "bot@im.bot",
                "ilink_user_id": "user@im.wechat",
                "baseurl": "https://ilinkai.weixin.qq.com",
            }

            wait_result = await wait_for_weixin_login(session_key=session_key, timeout_ms=5000)

            assert wait_result["connected"] is True
            assert wait_result["bot_token"] == "final-token"
