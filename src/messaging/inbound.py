"""Inbound message handling: contextToken store + MsgContext conversion."""
import json
import os

from src.storage.state_dir import resolve_state_dir
from src.util.logger import logger
from src.util.random import generate_id

# ---------------------------------------------------------------------------
# contextToken store (in-process + disk persistence)
# ---------------------------------------------------------------------------
_context_token_store: dict[str, str] = {}


def _context_token_key(account_id: str, user_id: str) -> str:
    return f"{account_id}:{user_id}"


def _resolve_context_token_file_path(account_id: str) -> str:
    return os.path.join(
        resolve_state_dir(),
        "wechat-acp",
        "accounts",
        f"{account_id}.context-tokens.json",
    )


def _persist_context_tokens(account_id: str) -> None:
    """Persist all context tokens for an account to disk."""
    prefix = f"{account_id}:"
    tokens: dict[str, str] = {}
    for k, v in _context_token_store.items():
        if k.startswith(prefix):
            tokens[k[len(prefix) :]] = v

    file_path = _resolve_context_token_file_path(account_id)
    try:
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f)
    except Exception as err:
        logger.warn(f"persistContextTokens: failed to write {file_path}: {err}")


def set_context_token(account_id: str, user_id: str, token: str) -> None:
    """Store a context token for a given account+user pair (memory + disk)."""
    k = _context_token_key(account_id, user_id)
    logger.debug(f"setContextToken: key={k}")
    _context_token_store[k] = token
    _persist_context_tokens(account_id)


def get_context_token(account_id: str, user_id: str) -> str | None:
    """Retrieve the cached context token for a given account+user pair."""
    k = _context_token_key(account_id, user_id)
    val = _context_token_store.get(k)
    logger.debug(f"getContextToken: key={k} found={val is not None}")
    return val


def clear_all_context_tokens() -> None:
    """Clear all in-memory context tokens."""
    _context_token_store.clear()


# ---------------------------------------------------------------------------
# Message context conversion
# ---------------------------------------------------------------------------
def is_media_item(item: "MessageItem") -> bool:  # type: ignore
    """Returns True if the message item is a media type."""
    return item.type in (
        MessageItemType.IMAGE,
        MessageItemType.VIDEO,
        MessageItemType.FILE,
        MessageItemType.VOICE,
    )


def _body_from_item_list(item_list: list["MessageItem"] | None) -> str:  # type: ignore
    """Extract text body from item_list (for slash command detection)."""
    if not item_list:
        return ""
    for item in item_list:
        if item.type == MessageItemType.TEXT and item.text_item and item.text_item.text is not None:
            text = str(item.text_item.text)
            ref = item.ref_msg
            if not ref:
                return text
            if ref.message_item and is_media_item(ref.message_item):
                return text
            parts = []
            if ref.title:
                parts.append(ref.title)
            if ref.message_item:
                ref_body = _body_from_item_list([ref.message_item])
                if ref_body:
                    parts.append(ref_body)
            if not parts:
                return text
            return f"[引用: {' | '.join(parts)}]\n{text}"
        # Voice with transcription
        if item.type == MessageItemType.VOICE and item.voice_item and item.voice_item.text:
            return item.voice_item.text
    return ""


from src.api.types import MessageItemType


# ---------------------------------------------------------------------------
# Inbound media opts
# ---------------------------------------------------------------------------
class WeixinInboundMediaOpts:
    """Populated by media download/decrypt step."""

    __slots__ = (
        "decrypted_pic_path",
        "decrypted_voice_path",
        "voice_media_type",
        "decrypted_file_path",
        "file_media_type",
        "decrypted_video_path",
    )

    def __init__(
        self,
        decrypted_pic_path: str | None = None,
        decrypted_voice_path: str | None = None,
        voice_media_type: str | None = None,
        decrypted_file_path: str | None = None,
        file_media_type: str | None = None,
        decrypted_video_path: str | None = None,
    ) -> None:
        self.decrypted_pic_path = decrypted_pic_path
        self.decrypted_voice_path = decrypted_voice_path
        self.voice_media_type = voice_media_type
        self.decrypted_file_path = decrypted_file_path
        self.file_media_type = file_media_type
        self.decrypted_video_path = decrypted_video_path


# ---------------------------------------------------------------------------
# WeixinMsgContext — inbound context passed to ACP core pipeline
# ---------------------------------------------------------------------------
class WeixinMsgContext:
    """Inbound context matching the MsgContext shape for the ACP core pipeline."""

    __slots__ = (
        "body",
        "from_",
        "to",
        "account_id",
        "originating_channel",
        "originating_to",
        "message_sid",
        "timestamp",
        "provider",
        "chat_type",
        "session_key",
        "context_token",
        "media_url",
        "media_path",
        "media_type",
        "command_body",
        "command_authorized",
    )

    def __init__(
        self,
        body: str = "",
        from_: str = "",
        to: str = "",
        account_id: str = "",
        originating_channel: str = "wechat-acp",
        originating_to: str = "",
        message_sid: str = "",
        timestamp: int | None = None,
        provider: str = "wechat-acp",
        chat_type: str = "direct",
        session_key: str | None = None,
        context_token: str | None = None,
        media_url: str | None = None,
        media_path: str | None = None,
        media_type: str | None = None,
        command_body: str | None = None,
        command_authorized: bool | None = None,
    ) -> None:
        self.body = body
        self.from_ = from_
        self.to = to
        self.account_id = account_id
        self.originating_channel = originating_channel
        self.originating_to = originating_to
        self.message_sid = message_sid
        self.timestamp = timestamp
        self.provider = provider
        self.chat_type = chat_type
        self.session_key = session_key
        self.context_token = context_token
        self.media_url = media_url
        self.media_path = media_path
        self.media_type = media_type
        self.command_body = command_body
        self.command_authorized = command_authorized

    @property
    def Body(self) -> str:
        return self.body

    @property
    def From(self) -> str:
        return self.from_

    @property
    def To(self) -> str:
        return self.to

    @property
    def AccountId(self) -> str:
        return self.account_id


def weixin_message_to_msg_context(
    msg: "WeixinMessage",  # type: ignore
    account_id: str,
    opts: WeixinInboundMediaOpts | None = None,
) -> WeixinMsgContext:
    """Convert WeixinMessage from getUpdates to inbound MsgContext for the core pipeline."""
    from_user_id = msg.from_user_id or ""

    ctx = WeixinMsgContext(
        body=_body_from_item_list(msg.item_list),
        from_=from_user_id,
        to=from_user_id,
        account_id=account_id,
        originating_channel="wechat-acp",
        originating_to=from_user_id,
        message_sid=generate_id("wechat-acp"),
        timestamp=msg.create_time_ms,
        provider="wechat-acp",
        chat_type="direct",
    )

    if msg.context_token:
        ctx.context_token = msg.context_token

    if opts:
        if opts.decrypted_pic_path:
            ctx.media_path = opts.decrypted_pic_path
            ctx.media_type = "image/*"
        elif opts.decrypted_video_path:
            ctx.media_path = opts.decrypted_video_path
            ctx.media_type = "video/mp4"
        elif opts.decrypted_file_path:
            ctx.media_path = opts.decrypted_file_path
            ctx.media_type = opts.file_media_type or "application/octet-stream"
        elif opts.decrypted_voice_path:
            ctx.media_path = opts.decrypted_voice_path
            ctx.media_type = opts.voice_media_type or "audio/wav"

    return ctx


def get_context_token_from_msg_context(ctx: WeixinMsgContext) -> str | None:
    """Extract the context_token from an inbound WeixinMsgContext."""
    return ctx.context_token
