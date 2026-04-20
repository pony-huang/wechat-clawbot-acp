"""Message routing module.

This module provides agent route resolution for inbound messages.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class RouteResult:
    """Result of a route resolution."""
    route: str
    session_key: str
    agent_id: str | None = None


def generate_session_key(account_id: str, user_id: str) -> str:
    """Generate a stable session key for an account-user pair.

    Args:
        account_id: The account ID
        user_id: The user ID

    Returns:
        A stable session key string
    """
    return f"{account_id}:{user_id}"


async def resolve_agent_route(
    ctx: dict[str, Any],
) -> RouteResult:
    """Resolve the agent route for an inbound message.

    This determines which agent should handle the message and creates
    a session key for tracking.

    Args:
        ctx: Message context containing account_id, from_user_id, etc.

    Returns:
        RouteResult with route name, session key, and optional agent_id
    """
    account_id = ctx.get("account_id", "default")
    from_user_id = ctx.get("from_user_id", "") or ctx.get("user_id", "")
    content = ctx.get("body", "") or ctx.get("text", "")

    # Generate a stable single-account session key
    session_key = generate_session_key(account_id, from_user_id)

    # For now, use default routing
    # In a full implementation, this would check content patterns,
    # user preferences, or other routing rules
    route = "default"
    agent_id = None

    # Check if this is a command (starts with /)
    if content.startswith("/"):
        route = "command"
        agent_id = "slash-command-agent"

    return RouteResult(
        route=route,
        session_key=session_key,
        agent_id=agent_id,
    )


def extract_session_account_id(session_key: str) -> str | None:
    """Extract account ID from a session key.

    Args:
        session_key: Session key in format "account_id:user_id:random"

    Returns:
        Account ID or None if invalid format
    """
    parts = session_key.split(":")
    if len(parts) >= 2:
        return parts[0]
    return None


def extract_session_user_id(session_key: str) -> str | None:
    """Extract user ID from a session key.

    Args:
        session_key: Session key in format "account_id:user_id:random"

    Returns:
        User ID or None if invalid format
    """
    parts = session_key.split(":")
    if len(parts) >= 2:
        return parts[1]
    return None
