"""Unit tests for state_dir module."""
import os
from pathlib import Path

import pytest

from src.storage.state_dir import (
    DEFAULT_STATE_DIR,
    resolve_preferred_wechatacp_tmp_dir,
    resolve_state_dir,
)


class TestResolveStateDir:
    """Tests for resolve_state_dir function."""

    def test_default_state_dir(self):
        """Test default state directory."""
        result = resolve_state_dir()
        assert ".wechat-acp" in result
        assert Path(result).is_absolute()

    def test_wechatacp_state_dir_env(self, monkeypatch):
        """Test WECHATACP_STATE_DIR env var takes priority."""
        monkeypatch.setenv("WECHATACP_STATE_DIR", "/custom/state")
        result = resolve_state_dir()
        assert result == "/custom/state"

    def test_wechatacp_state_dir_env_fallback(self, monkeypatch):
        """Test WECHATACP_STATE_DIR env var fallback."""
        monkeypatch.delenv("WECHATACP_STATE_DIR", raising=False)
        monkeypatch.setenv("WECHATACP_STATE_DIR", "/wechat-acp/state")
        result = resolve_state_dir()
        assert result == "/wechat-acp/state"


class TestResolvePreferredWechatacpTmpDir:
    """Tests for resolve_preferred_wechatacp_tmp_dir function."""

    def test_default_tmp_dir(self):
        """Test default tmp directory is under state dir."""
        result = resolve_preferred_wechatacp_tmp_dir()
        assert ".wechat-acp" in result
        assert "tmp" in result
        assert Path(result).is_absolute()

    def test_env_var_override(self, monkeypatch):
        """Test WECHATACP_TMP_DIR env var override."""
        monkeypatch.setenv("WECHATACP_TMP_DIR", "/custom/tmp")
        result = resolve_preferred_wechatacp_tmp_dir()
        assert result == "/custom/tmp"

    def test_creates_directory_if_missing(self, tmp_path, monkeypatch):
        """Test that directory is created if missing."""
        new_tmp = tmp_path / "new_tmp_dir"
        monkeypatch.setenv("WECHATACP_TMP_DIR", str(new_tmp))

        result = resolve_preferred_wechatacp_tmp_dir()

        assert new_tmp.exists()
        assert new_tmp.is_dir()
        assert result == str(new_tmp)

    def test_env_takes_priority_over_default(self, monkeypatch):
        """Test that env var takes priority over default."""
        monkeypatch.setenv("WECHATACP_TMP_DIR", "/priority/tmp")
        result = resolve_preferred_wechatacp_tmp_dir()
        assert result == "/priority/tmp"