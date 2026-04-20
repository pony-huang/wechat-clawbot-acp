"""Local protocol for the subset of channelRuntime used by this project.

The goal is behavioral compatibility with the upstream WeChat-ACP/Weixin design,
not source-level parity with the TypeScript implementation.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict


RuntimeFn = Callable[..., Any | Awaitable[Any]]


class RoutingRuntime(TypedDict):
    resolve_agent_route: RuntimeFn


class SessionRuntime(TypedDict):
    record_inbound_session: RuntimeFn
    resolve_store_path: RuntimeFn


class ReplyRuntime(TypedDict):
    finalize_inbound_context: RuntimeFn
    resolve_human_delay_config: RuntimeFn
    create_reply_dispatcher_with_typing: RuntimeFn
    with_reply_dispatcher: RuntimeFn
    dispatch_reply_from_config: RuntimeFn


class MediaRuntime(TypedDict, total=False):
    save_media_buffer: RuntimeFn | None


class ChannelRuntime(TypedDict, total=False):
    routing: RoutingRuntime
    session: SessionRuntime
    reply: ReplyRuntime
    media: MediaRuntime
