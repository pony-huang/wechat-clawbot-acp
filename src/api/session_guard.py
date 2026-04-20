"""Session guard: pause/resume when session expired (errcode -14)."""
from src.util.logger import logger

SESSION_EXPIRED_ERRCODE = -14
SESSION_PAUSE_DURATION_MS = 60 * 60 * 1000  # 1 hour

_pause_until_map: dict[str, int] = {}


def pause_session(account_id: str) -> None:
    """Pause all inbound/outbound API calls for accountId for one hour."""
    until = int((__import__("time").time() * 1000)) + SESSION_PAUSE_DURATION_MS
    _pause_until_map[account_id] = until
    logger.info(
        f"session-guard: paused accountId={account_id} until="
        f" {until} "
        f"({SESSION_PAUSE_DURATION_MS / 1000}s)"
    )

def get_remaining_pause_ms(account_id: str) -> int:
    """Milliseconds remaining until the pause expires (0 when not paused)."""
    until = _pause_until_map.get(account_id)
    if until is None:
        return 0
    remaining = until - int(__import__("time").time() * 1000)
    if remaining <= 0:
        _pause_until_map.pop(account_id, None)
        return 0
    return remaining


def _reset_for_test() -> None:
    """Reset internal state — only for tests."""
    _pause_until_map.clear()
