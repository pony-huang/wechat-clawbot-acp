"""Global Weixin runtime storage (mirrors TypeScript runtime.ts)."""
from src.channel_runtime_impl import build_default_channel_runtime
from src.runtime_protocol import ChannelRuntime
from src.util.logger import logger

_plugin_runtime: dict | None = None
WAIT_INTERVAL_MS = 100
DEFAULT_TIMEOUT_MS = 10_000


def set_weixin_runtime(next_: dict) -> None:
    """Set the global Weixin runtime (called from plugin register)."""
    global _plugin_runtime
    _plugin_runtime = next_
    logger.info("[runtime] setWeixinRuntime called, runtime set successfully")


def get_weixin_runtime() -> dict:
    """Get the global Weixin runtime (throws if not initialized)."""
    if not _plugin_runtime:
        raise RuntimeError("Weixin runtime not initialized")
    return _plugin_runtime


async def wait_for_weixin_runtime(timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """Wait for the Weixin runtime to be initialized (async polling)."""
    import asyncio
    import time

    start = time.time() * 1000
    while not _plugin_runtime:
        if (time.time() * 1000) - start > timeout_ms:
            raise RuntimeError("Weixin runtime initialization timeout")
        await asyncio.sleep(WAIT_INTERVAL_MS / 1000)
    return _plugin_runtime


async def resolve_weixin_channel_runtime(
    channel_runtime: ChannelRuntime | None = None,
    wait_timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> ChannelRuntime:
    """Resolve PluginRuntime['channel'] for the long-poll monitor.

    Resolution order mirrors the TypeScript runtime shape used by the Weixin
    bridge today: explicit gateway injection first, then the registered plugin
    runtime, then the built-in local runtime implementation.
    """
    from src.util.logger import logger

    if channel_runtime:
        logger.debug("[runtime] channelRuntime from gateway context")
        return channel_runtime
    if _plugin_runtime:
        logger.debug("[runtime] channelRuntime from register() global")
        runtime_channel = _plugin_runtime.get("channel", {})
        if runtime_channel:
            return runtime_channel
    logger.info("[runtime] channelRuntime unavailable from ctx/runtime, using built-in runtime")
    return build_default_channel_runtime()
