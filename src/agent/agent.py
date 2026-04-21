import asyncio
import asyncio.subprocess as aio_subprocess
import base64
import contextlib
import logging
import mimetypes
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Coroutine

from acp import PROTOCOL_VERSION, Client, RequestError, connect_to_agent, RequestPermissionResponse
from acp.core import ClientSideConnection, DEFAULT_STDIO_BUFFER_LIMIT_BYTES
from acp.helpers import text_block
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    ClientCapabilities,
    ConfigOptionUpdate,
    CreateTerminalResponse,
    CurrentModeUpdate,
    EnvVariable,
    Implementation,
    KillTerminalResponse,
    PermissionOption,
    PromptResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    RequestPermissionResponse,
    ResourceContentBlock,
    SessionInfoUpdate,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCall,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse, DeniedOutcome, AllowedOutcome,
)

from src.media_ops.upload import download_remote_image_to_temp
from src.messaging.error_notice import send_weixin_error_notice
from src.messaging.send import send_message_weixin
from src.messaging.send_media import send_weixin_media_file
from src.util.logger import logger


@dataclass(slots=True)
class WeixinAcpContext:
    to_user_id: str
    base_url: str
    token: str | None = None
    context_token: str | None = None
    typing_ticket: str | None = None
    cdn_base_url: str | None = None
    account_id: str | None = None


@dataclass(slots=True)
class WeixinReplyPayload:
    text: str | None = None
    media_url: str | None = None


_bridge_conn: ClientSideConnection | None = None
_acp_client_impl: "AcpClient | None" = None
_active_session_id: str | None = None
_current_weixin_context: WeixinAcpContext | None = None
_reply_activity_listeners: list[Callable[[], None | Awaitable[None]]] = []
_reply_delivery_hook: Callable[[WeixinReplyPayload], None | Awaitable[None]] | None = None


def set_agent_connection(conn: ClientSideConnection | None) -> None:
    global _bridge_conn
    _bridge_conn = conn


def get_agent_connection() -> ClientSideConnection | None:
    return _bridge_conn


def set_acp_client(client: "AcpClient | None") -> None:
    global _acp_client_impl
    _acp_client_impl = client


def get_acp_client() -> "AcpClient | None":
    return _acp_client_impl


async def flush_pending_message() -> None:
    """Flush any pending message buffer in the ACP client."""
    client = get_acp_client()
    if client:
        await client._flush_pending_message()


def set_active_session_id(session_id: str | None) -> None:
    global _active_session_id
    _active_session_id = session_id


def get_active_session_id() -> str | None:
    return _active_session_id


def set_weixin_context(context: WeixinAcpContext | None) -> None:
    global _current_weixin_context
    _current_weixin_context = context


def get_weixin_context() -> WeixinAcpContext | None:
    return _current_weixin_context


def set_reply_delivery_hook(
    hook: Callable[[WeixinReplyPayload], None | Awaitable[None]] | None,
) -> None:
    global _reply_delivery_hook
    _reply_delivery_hook = hook


def get_reply_delivery_hook() -> Callable[[WeixinReplyPayload], None | Awaitable[None]] | None:
    return _reply_delivery_hook


def register_reply_activity_listener(listener: Callable[[], None | Awaitable[None]]) -> None:
    _reply_activity_listeners.append(listener)


def unregister_reply_activity_listener(listener: Callable[[], None | Awaitable[None]]) -> None:
    with contextlib.suppress(ValueError):
        _reply_activity_listeners.remove(listener)


async def _notify_reply_activity() -> None:
    for listener in tuple(_reply_activity_listeners):
        try:
            result = listener()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.debug(f"[agent] reply activity listener failed: {exc}")


async def notify_reply_activity() -> None:
    await _notify_reply_activity()


def clear_bridge_state() -> None:
    _reply_activity_listeners.clear()
    set_reply_delivery_hook(None)
    set_agent_connection(None)
    set_acp_client(None)
    set_active_session_id(None)
    set_weixin_context(None)


async def prompt_active_session(prompt_text: str) -> PromptResponse:
    conn = get_agent_connection()
    session_id = get_active_session_id()
    if conn is None or not session_id:
        raise RuntimeError("ACP bridge is not connected")
    return await conn.prompt(session_id=session_id, prompt=[text_block(prompt_text)])


def _resolve_reply_temp_dir() -> Path:
    state_root = Path(os.environ.get("WECHATACP_STATE_DIR") or Path.home() / ".wechat-acp")
    temp_dir = state_root / "wechat-acp" / "reply-media"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _guess_media_extension(mime_type: str | None, uri_hint: str | None = None) -> str:
    guessed = mimetypes.guess_extension(mime_type or "")
    if guessed:
        return guessed
    if uri_hint:
        suffix = Path(uri_hint).suffix
        if suffix:
            return suffix
    return ".bin"


def _materialize_base64_media(data: str, mime_type: str | None, uri_hint: str | None = None) -> str:
    output_path = _resolve_reply_temp_dir() / (
        f"acp-reply-{os.urandom(8).hex()}{_guess_media_extension(mime_type, uri_hint)}"
    )
    output_path.write_bytes(base64.b64decode(data))
    return str(output_path)


def _build_resource_caption(block: ResourceContentBlock) -> str:
    parts = [block.title, block.description]
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _is_remote_media_url(media_url: str) -> bool:
    return media_url.startswith("http://") or media_url.startswith("https://")


def _normalize_local_media_path(media_url: str) -> str:
    if media_url.startswith("file://"):
        return media_url.removeprefix("file://")
    return media_url


async def _send_text_to_weixin(text: str) -> None:
    context = get_weixin_context()
    if context is None:
        logger.warn("[agent] dropping ACP text update because WeChat context is not set")
        return
    if not text:
        return

    await send_message_weixin(
        to=context.to_user_id,
        text=text,
        opts={
            "base_url": context.base_url,
            "token": context.token,
            "context_token": context.context_token,
        },
    )
    await _notify_reply_activity()


async def _send_media_to_weixin(payload: WeixinReplyPayload) -> None:
    context = get_weixin_context()
    if context is None:
        logger.warn("[agent] dropping ACP media update because WeChat context is not set")
        return
    if not payload.media_url:
        return
    if not context.cdn_base_url:
        raise RuntimeError("WeChat CDN base URL is not configured for media replies")
    media_path = payload.media_url
    if _is_remote_media_url(media_path):
        media_path = await download_remote_image_to_temp(media_path, str(_resolve_reply_temp_dir()))
    else:
        media_path = _normalize_local_media_path(media_path)

    await send_weixin_media_file(
        file_path=media_path,
        to=context.to_user_id,
        text=payload.text or "",
        opts={
            "base_url": context.base_url,
            "token": context.token,
            "context_token": context.context_token,
        },
        cdn_base_url=context.cdn_base_url,
    )
    await _notify_reply_activity()


async def deliver_reply_payload(payload: WeixinReplyPayload) -> None:
    if payload.media_url:
        await _send_media_to_weixin(payload)
        return
    await _send_text_to_weixin(payload.text or "")


async def _deliver_payload_to_weixin(payload: WeixinReplyPayload) -> None:
    deliver = get_reply_delivery_hook() or deliver_reply_payload
    context = get_weixin_context()
    try:
        result = deliver(payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.error(f"[agent] failed to deliver ACP reply payload: {exc}")
        if context is None:
            return
        await send_weixin_error_notice(
            to=context.to_user_id,
            context_token=context.context_token,
            message="抱歉，刚才的回复发送失败了，请稍后重试。",
            base_url=context.base_url,
            token=context.token,
        )
        await _notify_reply_activity()


def _extract_reply_payload(update: AgentMessageChunk | AgentThoughtChunk) -> WeixinReplyPayload | None:
    content = update.content
    if isinstance(content, TextContentBlock):
        return WeixinReplyPayload(text=content.text or "")

    if isinstance(content, ImageContentBlock):
        return WeixinReplyPayload(
            media_url=content.uri or _materialize_base64_media(
                content.data,
                content.mime_type,
                content.uri,
            )
        )

    if isinstance(content, ResourceContentBlock):
        return WeixinReplyPayload(
            text=_build_resource_caption(content),
            media_url=content.uri,
        )

    if isinstance(content, EmbeddedResourceContentBlock):
        resource = content.resource
        if hasattr(resource, "blob"):
            return WeixinReplyPayload(
                media_url=_materialize_base64_media(
                    resource.blob,
                    getattr(resource, "mime_type", None),
                    getattr(resource, "uri", None),
                )
            )
        if hasattr(resource, "text"):
            return WeixinReplyPayload(text=resource.text or "")

    logger.debug(
        f"[agent] ignoring unsupported ACP content block type: {type(content).__name__}"
    )
    return None

def _slice_text(content: str, line: int | None, limit: int | None) -> str:
    lines = content.splitlines()
    start = 0
    if line:
        start = max(line - 1, 0)
    end = len(lines)
    if limit:
        end = min(start + limit, end)
    return "\n".join(lines[start:end])

def _pick_preferred_option(options: Iterable[PermissionOption]) -> PermissionOption | None:
    best: PermissionOption | None = None
    for option in options:
        if option.kind in {"allow_once", "allow_always"}:
            return option
        best = best or option
    return best

class AcpClient(Client):
    def __init__(self, auto_approve: bool = True) -> None:
        super().__init__()
        self._pending_message_buffer: list[str] = []
        self._pending_message_id: str | None = None
        self._auto_approve = auto_approve

    async def request_permission(
        self, options: list[PermissionOption], session_id: str, tool_call: ToolCall, **kwargs: Any
    ) -> RequestPermissionResponse | None:
        # Auto-approve all permission requests by default
        if self._auto_approve:
            option = _pick_preferred_option(options)
            if option is None:
                logger.info(f"[agent] permission requested but no valid option, denying")
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            logger.info(f"[agent] permission auto-approved: {option.name} ({option.kind})")
            return RequestPermissionResponse(outcome=AllowedOutcome(option_id=option.option_id, outcome="selected"))

        # If auto_approve is False, fall back to deny (interactive not supported)
        logger.info(f"[agent] permission denied (auto_approve=False)")
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        pathlib_path = Path(path)
        if not pathlib_path.is_absolute():
            raise RequestError.invalid_params({"path": pathlib_path, "reason": "path must be absolute"})
        pathlib_path.parent.mkdir(parents=True, exist_ok=True)
        pathlib_path.write_text(content)
        logger.info(f"[Agent] Wrote {pathlib_path} ({len(content)} bytes)")
        return WriteTextFileResponse()

    async def read_text_file(
        self, path: str, session_id: str, limit: int | None = None, line: int | None = None, **kwargs: Any
    ) -> ReadTextFileResponse:
        pathlib_path = Path(path)
        if not pathlib_path.is_absolute():
            raise RequestError.invalid_params({"path": pathlib_path, "reason": "path must be absolute"})
        text = pathlib_path.read_text()
        logger.info(f"[Agent] Read {pathlib_path} ({len(text)} bytes)")
        if line is not None or limit is not None:
            text = _slice_text(text, line, limit)
        return ReadTextFileResponse(content=text)

    # Optional / terminal-related methods
    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        return CreateTerminalResponse(terminal_id="term-1")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> TerminalOutputResponse:
        return TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        return ReleaseTerminalResponse()

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        return WaitForTerminalExitResponse()

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> KillTerminalResponse | None:
        return KillTerminalResponse()

    async def _flush_pending_message(self) -> None:
        """发送缓冲区的消息并清空缓冲区。"""
        if self._pending_message_buffer:
            full_text = "".join(self._pending_message_buffer)
            self._pending_message_buffer = []
            self._pending_message_id = None
            if full_text:
                await _deliver_payload_to_weixin(WeixinReplyPayload(text=full_text))

    async def session_update(
        self,
        session_id: str,
        update: UserMessageChunk
        | AgentMessageChunk
        | AgentThoughtChunk
        | ToolCallStart
        | ToolCallProgress
        | AgentPlanUpdate
        | AvailableCommandsUpdate
        | CurrentModeUpdate
        | ConfigOptionUpdate
        | SessionInfoUpdate
        | UsageUpdate,
        **kwargs: Any,
    ) -> None:
        logger.info(f"[agent] received ACP session update: {update}")

        if isinstance(update, AgentMessageChunk):
            payload = _extract_reply_payload(update)
            if payload is None:
                return
            if payload.media_url:
                if self._pending_message_buffer:
                    await self._flush_pending_message()
                await _deliver_payload_to_weixin(payload)
                return

            text = payload.text or ""

            # 部分ACP MessageId是 None, 需要处理, 默认使用 default
            current_message_id = update.message_id or "default"

            # 如果 message_id 发生变化，说明之前的消息完整了
            if self._pending_message_id is not None and current_message_id != self._pending_message_id:
                await self._flush_pending_message()

            # When the upstream update does not provide a message_id, treat it as
            # a complete message rather than an open-ended stream.
            if current_message_id is None:
                self._pending_message_buffer.append(text)
                await self._flush_pending_message()
                return

            # 添加到缓冲区
            self._pending_message_buffer.append(text)
            self._pending_message_id = current_message_id
            return

        # 收到其他类型的事件（如 ToolCallStart、UserMessageChunk 等），说明之前的消息完整了
        if self._pending_message_buffer:
            await self._flush_pending_message()

    async def ext_method(self, method: str, params: dict) -> dict:
        logger.info(f"[agent] received external method call: {method}")
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict) -> None:
        logger.info(f"[agent] received external notification: {method}")
        raise RequestError.method_not_found(method)


async def connect_external_agent(
    program: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[ClientSideConnection, asyncio.subprocess.Process]:
    spawn_args = list(args or [])
    program_path = Path(program)
    spawn_program = program

    if program_path.exists() and not os.access(program_path, os.X_OK):
        spawn_program = sys.executable
        spawn_args = [str(program_path), *spawn_args]

    proc = await asyncio.create_subprocess_exec(
        spawn_program,
        *spawn_args,
        stdin=aio_subprocess.PIPE,
        stdout=aio_subprocess.PIPE,
        limit=DEFAULT_STDIO_BUFFER_LIMIT_BYTES,
        cwd=cwd,
        env=env,
    )

    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Agent process does not expose stdio pipes")

    client_impl = AcpClient()
    conn = connect_to_agent(client_impl, proc.stdin, proc.stdout)
    await conn.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(),
        client_info=Implementation(name="wechat-clawbot-acp", title="WeChat Clawbot ACP", version="0.1.0"),
    )
    session = await conn.new_session(mcp_servers=[], cwd=cwd or os.getcwd())

    set_agent_connection(conn)
    set_acp_client(client_impl)
    set_active_session_id(session.session_id)
    return conn, proc


async def main(argv: list[str]) -> int | None:
    logging.basicConfig(level=logging.INFO)

    if len(argv) < 2:
        print("Usage: python -m src.agent.agent AGENT_PROGRAM [ARGS...]", file=sys.stderr)
        return 2

    proc: asyncio.subprocess.Process | None = None
    try:
        _, proc = await connect_external_agent(argv[1], argv[2:])
        if proc.returncode is None:
            await proc.wait()
        return proc.returncode or 0
    finally:
        clear_bridge_state()
        if proc is not None and proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
