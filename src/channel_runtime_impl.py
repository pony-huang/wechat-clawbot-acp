"""Minimal channelRuntime implementation for the Weixin ACP bridge.

This fills the subset of the WeChat-ACP channel runtime that the current Python
bridge can use today: routing, session bookkeeping, and basic reply helpers.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import Any

from src.agent.agent import (
    WeixinReplyPayload,
    deliver_reply_payload,
    get_reply_delivery_hook,
    set_reply_delivery_hook,
)
from src.messaging.routing import resolve_agent_route
from src.messaging.session import record_inbound_session
from src.runtime_protocol import ChannelRuntime
from src.util.async_helpers import maybe_await


async def _dispatch_reply_from_config(params: dict[str, Any]) -> None:
    """Minimal reply dispatch for the ACP bridge.

    The external ACP agent is already connected globally. Dispatching a reply
    therefore means forwarding the inbound prompt text into the active ACP
    session and letting `src.agent.agent.AcpClient.session_update()` stream the
    outbound chunks back to Weixin.
    """
    ctx = params.get("ctx")
    if ctx is None:
        return

    prompt_text = getattr(ctx, "body", "") or ""
    if not prompt_text:
        return

    from src.agent.agent import (
        flush_pending_message,
        get_agent_connection,
        prompt_active_session,
        register_reply_activity_listener,
        unregister_reply_activity_listener,
    )

    if get_agent_connection() is None:
        raise RuntimeError("ACP bridge is not connected")

    first_reply_event = asyncio.Event()
    dispatcher = params.get("dispatcher") or {}
    previous_delivery_hook = get_reply_delivery_hook()

    async def _deliver(payload: WeixinReplyPayload) -> None:
        deliver = dispatcher.get("deliver")
        if deliver:
            result = deliver(payload)
            if inspect.isawaitable(result):
                await result
            return
        await deliver_reply_payload(payload)

    async def _mark_reply_activity() -> None:
        first_reply_event.set()

    set_reply_delivery_hook(_deliver)
    register_reply_activity_listener(_mark_reply_activity)
    try:
        response = await prompt_active_session(prompt_text)
        # Check stop_reason to determine if conversation ended normally
        if response.stop_reason:
            await flush_pending_message()
        wait_timeout = (
            params.get("reply_options", {}).get("await_first_reply_timeout_seconds")
            or 15.0
        )
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(first_reply_event.wait(), timeout=wait_timeout)
    finally:
        unregister_reply_activity_listener(_mark_reply_activity)
        set_reply_delivery_hook(previous_delivery_hook)


def _create_reply_dispatcher_with_typing(params: dict[str, Any]) -> dict[str, Any]:
    """Create a minimal reply dispatcher wrapper.

    The current ACP bridge does not use an in-process dispatcher to emit final
    messages; the connected ACP agent streams them asynchronously. We still keep
    the object shape compatible with the TypeScript runtime so `process_message`
    can transition to the richer path incrementally.
    """
    dispatcher: dict[str, Any] = {
        "deliver": params.get("deliver") or deliver_reply_payload,
        "typing_callbacks": params.get("typing_callbacks") or {},
        "typing_keepalive_interval_ms": params.get("typing_keepalive_interval_ms", 3000),
        "typing_keepalive_task": None,
        "dispatch_idle": False,
    }
    return {
        "dispatcher": dispatcher,
        "reply_options": {
            "await_first_reply_timeout_seconds": params.get(
                "await_first_reply_timeout_seconds",
                15.0,
            ),
        },
        "mark_dispatch_idle": lambda: dispatcher.__setitem__("dispatch_idle", True),
    }


async def _start_dispatcher_typing(dispatcher: dict[str, Any]) -> None:
    callbacks = dispatcher.get("typing_callbacks") or {}
    start = callbacks.get("start")
    keepalive = callbacks.get("keepalive") or start
    interval_ms = dispatcher.get("typing_keepalive_interval_ms", 3000)

    if start:
        await maybe_await(start)

    if not keepalive:
        return

    async def _keepalive_loop() -> None:
        try:
            while not dispatcher.get("dispatch_idle"):
                await asyncio.sleep(interval_ms / 1000)
                if dispatcher.get("dispatch_idle"):
                    break
                await maybe_await(keepalive)
        except asyncio.CancelledError:
            raise

    dispatcher["typing_keepalive_task"] = asyncio.create_task(_keepalive_loop())


async def _stop_dispatcher_typing(dispatcher: dict[str, Any]) -> None:
    keepalive_task = dispatcher.get("typing_keepalive_task")
    if keepalive_task is not None:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task
        dispatcher["typing_keepalive_task"] = None

    stop = (dispatcher.get("typing_callbacks") or {}).get("stop")
    if stop:
        await maybe_await(stop)


async def _with_reply_dispatcher(params: dict[str, Any]) -> Any:
    dispatcher = params.get("dispatcher") or {}
    run = params.get("run")

    await _start_dispatcher_typing(dispatcher)
    try:
        if run:
            return await maybe_await(run)
        return None
    finally:
        dispatcher["dispatch_idle"] = True
        await _stop_dispatcher_typing(dispatcher)


async def _record_inbound_session_with_runtime(params: dict[str, Any]) -> Any:
    ctx = params.get("ctx")
    user_id = getattr(ctx, "from_", "") if ctx is not None else ""
    return await record_inbound_session(
        session_key=params["session_key"],
        account_id=params.get("account_id") or getattr(ctx, "account_id", ""),
        user_id=user_id,
        metadata=params.get("metadata"),
    )


def _resolve_store_path(store: Any, params: dict[str, Any]) -> str:
    agent_id = params.get("agent_id") or "default"
    return f"state://wechat-acp/sessions/{agent_id}"


def build_default_channel_runtime() -> ChannelRuntime:
    return {
        "routing": {
            "resolve_agent_route": resolve_agent_route,
        },
        "session": {
            "record_inbound_session": _record_inbound_session_with_runtime,
            "resolve_store_path": _resolve_store_path,
        },
        "reply": {
            "finalize_inbound_context": lambda ctx: ctx,
            "resolve_human_delay_config": lambda cfg, agent_id=None: 0,
            "create_reply_dispatcher_with_typing": _create_reply_dispatcher_with_typing,
            "with_reply_dispatcher": _with_reply_dispatcher,
            "dispatch_reply_from_config": _dispatch_reply_from_config,
        },
        "media": {
            "save_media_buffer": None,
        },
    }
