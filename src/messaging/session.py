"""Session recording module.

This module provides session recording for inbound message handling.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.storage.state_dir import resolve_state_dir


_session_storage: dict[str, dict[str, Any]] = {}
_sessions_loaded = False


def _resolve_session_store_path() -> str:
    return os.path.join(resolve_state_dir(), "wechat-acp", "sessions.json")


def _serialize_session_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "created_at": record["created_at"].isoformat(),
        "last_activity": record["last_activity"].isoformat(),
    }


def _deserialize_session_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "created_at": datetime.fromisoformat(record["created_at"]),
        "last_activity": datetime.fromisoformat(record["last_activity"]),
    }


def _ensure_sessions_loaded() -> None:
    global _sessions_loaded
    if _sessions_loaded:
        return

    file_path = _resolve_session_store_path()
    try:
        if os.path.exists(file_path):
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                for session_key, record in payload.items():
                    if not isinstance(record, dict):
                        continue
                    try:
                        _session_storage[session_key] = _deserialize_session_record(record)
                    except Exception:
                        continue
    except Exception:
        pass

    _sessions_loaded = True


def _persist_sessions() -> None:
    file_path = _resolve_session_store_path()
    dir_path = os.path.dirname(file_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                session_key: _serialize_session_record(record)
                for session_key, record in _session_storage.items()
            },
            f,
            indent=2,
            ensure_ascii=False,
        )


@dataclass
class SessionRecord:
    """Record of an inbound session."""
    session_key: str
    account_id: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


async def record_inbound_session(
    session_key: str,
    account_id: str,
    user_id: str,
    metadata: dict[str, Any] | None = None,
) -> SessionRecord:
    """Record an inbound session.

    Args:
        session_key: Unique session identifier
        account_id: Account ID
        user_id: User ID
        metadata: Optional session metadata

    Returns:
        SessionRecord that was created/updated
    """
    _ensure_sessions_loaded()
    now = datetime.now()

    if session_key in _session_storage:
        # Update existing session
        record = _session_storage[session_key]
        record["last_activity"] = now
        if metadata:
            record["metadata"].update(metadata)
        _persist_sessions()
        return SessionRecord(**record)

    # Create new session
    record = SessionRecord(
        session_key=session_key,
        account_id=account_id,
        user_id=user_id,
        created_at=now,
        last_activity=now,
        metadata=metadata or {},
    )
    _session_storage[session_key] = {
        "session_key": record.session_key,
        "account_id": record.account_id,
        "user_id": record.user_id,
        "created_at": record.created_at,
        "last_activity": record.last_activity,
        "metadata": record.metadata,
    }
    _persist_sessions()
    return record


async def get_session(session_key: str) -> SessionRecord | None:
    """Get a session by key.

    Args:
        session_key: Session identifier

    Returns:
        SessionRecord or None if not found
    """
    _ensure_sessions_loaded()
    if session_key not in _session_storage:
        return None
    data = _session_storage[session_key]
    return SessionRecord(**data)


async def update_session_activity(session_key: str) -> bool:
    """Update last activity timestamp for a session.

    Args:
        session_key: Session identifier

    Returns:
        True if session was found and updated, False otherwise
    """
    _ensure_sessions_loaded()
    if session_key not in _session_storage:
        return False
    _session_storage[session_key]["last_activity"] = datetime.now()
    _persist_sessions()
    return True


def clear_all_sessions() -> None:
    """Clear all sessions (for testing)."""
    global _sessions_loaded
    _session_storage.clear()
    _sessions_loaded = False
    file_path = _resolve_session_store_path()
    if os.path.exists(file_path):
        os.unlink(file_path)
