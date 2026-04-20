"""Unit tests for the session module."""
import shutil
import uuid
from pathlib import Path

from src.messaging.session import (
    clear_all_sessions,
    get_session,
    record_inbound_session,
    update_session_activity,
)


class TestSessionRecording:
    """Tests for session module."""

    def setup_method(self):
        clear_all_sessions()

    def _state_dir(self) -> Path:
        state_dir = Path("build") / "test-state" / f"session-{uuid.uuid4().hex}"
        shutil.rmtree(state_dir, ignore_errors=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    async def test_record_new_session(self, monkeypatch):
        """Test recording a new session."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(self._state_dir()))
        result = await record_inbound_session(
            session_key="key1",
            account_id="acc1",
            user_id="user1",
        )
        assert result.session_key == "key1"
        assert result.account_id == "acc1"
        assert result.user_id == "user1"

    async def test_update_existing_session(self, monkeypatch):
        """Test updating an existing session."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(self._state_dir()))
        await record_inbound_session("key1", "acc1", "user1")
        result = await record_inbound_session("key1", "acc1", "user1")
        assert result.session_key == "key1"

    async def test_get_session(self, monkeypatch):
        """Test getting a session."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(self._state_dir()))
        await record_inbound_session("key1", "acc1", "user1")
        result = await get_session("key1")
        assert result is not None
        assert result.user_id == "user1"

    async def test_get_nonexistent_session(self, monkeypatch):
        """Test getting nonexistent session returns None."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(self._state_dir()))
        result = await get_session("nonexistent")
        assert result is None

    async def test_update_session_activity(self, monkeypatch):
        """Test updating session activity."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(self._state_dir()))
        await record_inbound_session("key1", "acc1", "user1")
        result = await update_session_activity("key1")
        assert result is True

    async def test_update_activity_nonexistent(self, monkeypatch):
        """Test updating nonexistent session."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(self._state_dir()))
        result = await update_session_activity("nonexistent")
        assert result is False

    async def test_record_persists_session_to_disk(self, monkeypatch):
        """Test session records are persisted to disk."""
        state_dir = self._state_dir()
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(state_dir))

        await record_inbound_session("key1", "acc1", "user1", {"context_token": "ctx-1"})

        store_path = state_dir / "wechat-acp" / "sessions.json"
        assert store_path.exists()
        assert "key1" in store_path.read_text(encoding="utf-8")

    async def test_get_session_restores_from_disk_after_cache_reset(self, monkeypatch):
        """Test persisted sessions are reloaded after an in-memory reset."""
        from src.messaging import session as session_module

        state_dir = self._state_dir()
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(state_dir))

        await record_inbound_session("key1", "acc1", "user1")
        session_module._session_storage.clear()
        session_module._sessions_loaded = False

        restored = await get_session("key1")
        assert restored is not None
        assert restored.user_id == "user1"

    async def test_clear_all_sessions_removes_persisted_file(self, monkeypatch):
        """Test clearing sessions removes the persisted session file."""
        state_dir = self._state_dir()
        monkeypatch.setenv("WECHATACP_STATE_DIR", str(state_dir))

        await record_inbound_session("key1", "acc1", "user1")
        clear_all_sessions()

        store_path = state_dir / "wechat-acp" / "sessions.json"
        assert not store_path.exists()
