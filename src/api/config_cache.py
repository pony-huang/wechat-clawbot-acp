"""Per-user getConfig cache with periodic random refresh and exponential backoff retry."""
import time
from typing import Callable

from src.api.client import get_config
from src.util.logger import logger

CONFIG_CACHE_TTL_MS = 24 * 60 * 60 * 1000
CONFIG_CACHE_INITIAL_RETRY_MS = 2_000
CONFIG_CACHE_MAX_RETRY_MS = 60 * 60 * 1000


class CachedConfig:
    """Subset of getConfig fields needed by callers."""

    __slots__ = ("typing_ticket",)

    def __init__(self, typing_ticket: str = "") -> None:
        self.typing_ticket = typing_ticket


class _ConfigCacheEntry:
    __slots__ = ("config", "ever_succeeded", "next_fetch_at", "retry_delay_ms")

    def __init__(self, config: CachedConfig, ever_succeeded: bool, next_fetch_at: float, retry_delay_ms: float) -> None:
        self.config = config
        self.ever_succeeded = ever_succeeded
        self.next_fetch_at = next_fetch_at
        self.retry_delay_ms = retry_delay_ms


class WeixinConfigManager:
    """Per-user getConfig cache with 24h random refresh and exponential backoff retry."""

    def __init__(self, api_opts: dict, log: Callable[[str], None]) -> None:
        self._api_opts = api_opts
        self._log = log
        self._cache: dict[str, _ConfigCacheEntry] = {}

    @staticmethod
    def _cache_key(user_id: str, context_token: str | None) -> str:
        return f"{user_id}\n{context_token or ''}"

    async def get_for_user(self, user_id: str, context_token: str | None = None) -> CachedConfig:
        """Fetch config for user_id, using cache if still valid."""
        now_ms = time.time() * 1000
        cache_key = self._cache_key(user_id, context_token)
        entry = self._cache.get(cache_key)
        should_fetch = entry is None or now_ms >= entry.next_fetch_at

        if should_fetch:
            fetch_ok = False
            try:
                resp = await get_config(
                    base_url=self._api_opts["base_url"],
                    token=self._api_opts.get("token"),
                    ilink_user_id=user_id,
                    context_token=context_token,
                )
                if resp.ret == 0:
                    typing_ticket = resp.typing_ticket or ""
                    self._cache[cache_key] = _ConfigCacheEntry(
                        config=CachedConfig(typing_ticket=typing_ticket),
                        ever_succeeded=True,
                        next_fetch_at=now_ms + CONFIG_CACHE_TTL_MS * (0.5 + __import__("random").random()),
                        retry_delay_ms=CONFIG_CACHE_INITIAL_RETRY_MS,
                    )
                    self._log(
                        f"[weixin] config {'refreshed' if entry and entry.ever_succeeded else 'cached'} "
                        f"for {user_id} context={bool(context_token)} typing_ticket={bool(typing_ticket)}"
                    )
                    fetch_ok = True
            except Exception as err:
                self._log(f"[weixin] getConfig failed for {user_id} (ignored): {err}")

            if not fetch_ok:
                prev_delay = entry.retry_delay_ms if entry else CONFIG_CACHE_INITIAL_RETRY_MS
                next_delay = min(prev_delay * 2, CONFIG_CACHE_MAX_RETRY_MS)
                if entry:
                    entry.next_fetch_at = now_ms + next_delay
                    entry.retry_delay_ms = next_delay
                else:
                    self._cache[cache_key] = _ConfigCacheEntry(
                        config=CachedConfig(),
                        ever_succeeded=False,
                        next_fetch_at=now_ms + CONFIG_CACHE_INITIAL_RETRY_MS,
                        retry_delay_ms=CONFIG_CACHE_INITIAL_RETRY_MS,
                    )

        return self._cache.get(cache_key).config if self._cache.get(cache_key) else CachedConfig()
