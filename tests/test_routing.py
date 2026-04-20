"""Unit tests for routing module."""
import pytest

from src.messaging.routing import (
    RouteResult,
    extract_session_account_id,
    extract_session_user_id,
    generate_session_key,
    resolve_agent_route,
)


class TestGenerateSessionKey:
    """Tests for generate_session_key."""

    def test_session_key_format(self):
        """Test session key has correct format."""
        key = generate_session_key("account1", "user1")
        parts = key.split(":")
        assert len(parts) == 2
        assert parts[0] == "account1"
        assert parts[1] == "user1"

    def test_stable_keys(self):
        """Test that each call generates the same key for one account/user pair."""
        key1 = generate_session_key("account1", "user1")
        key2 = generate_session_key("account1", "user1")
        assert key1 == key2


class TestResolveAgentRoute:
    """Tests for resolve_agent_route."""

    async def test_default_route(self):
        """Test default route for regular message."""
        ctx = {"account_id": "acc1", "from_user_id": "user1", "body": "hello"}
        result = await resolve_agent_route(ctx)
        assert result.route == "default"
        assert result.session_key == "acc1:user1"
        assert result.agent_id is None

    async def test_command_route(self):
        """Test command route for / commands."""
        ctx = {"account_id": "acc1", "from_user_id": "user1", "body": "/help"}
        result = await resolve_agent_route(ctx)
        assert result.route == "command"
        assert result.agent_id == "slash-command-agent"


class TestExtractSessionAccountId:
    """Tests for extract_session_account_id."""

    def test_extract_valid_key(self):
        """Test extracting from valid session key."""
        assert extract_session_account_id("acc:user:random") == "acc"

    def test_extract_invalid_key(self):
        """Test extracting from invalid key returns None."""
        assert extract_session_account_id("invalid") is None


class TestExtractSessionUserId:
    """Tests for extract_session_user_id."""

    def test_extract_valid_key(self):
        """Test extracting from valid session key."""
        assert extract_session_user_id("acc:user:random") == "user"

    def test_extract_invalid_key(self):
        """Test extracting from invalid key returns None."""
        assert extract_session_user_id("invalid") is None


class TestRouteResult:
    """Tests for RouteResult dataclass."""

    def test_default_route_result(self):
        """Test creating route result."""
        result = RouteResult(route="default", session_key="key123")
        assert result.route == "default"
        assert result.session_key == "key123"
        assert result.agent_id is None

    def test_route_result_with_agent(self):
        """Test creating route result with agent."""
        result = RouteResult(route="command", session_key="key123", agent_id="agent1")
        assert result.agent_id == "agent1"
