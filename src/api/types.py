"""Weixin protocol types — Pydantic models mirroring TypeScript interfaces."""
from pydantic import BaseModel
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Upload media types
# ---------------------------------------------------------------------------
class UploadMediaType:
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


# ---------------------------------------------------------------------------
# Message types & states
# ---------------------------------------------------------------------------
class MessageType:
    NONE: Literal[0] = 0
    USER: Literal[1] = 1
    BOT: Literal[2] = 2


class MessageItemType:
    NONE: Literal[0] = 0
    TEXT: Literal[1] = 1
    IMAGE: Literal[2] = 2
    VOICE: Literal[3] = 3
    FILE: Literal[4] = 4
    VIDEO: Literal[5] = 5


class MessageState:
    NEW: Literal[0] = 0
    GENERATING: Literal[1] = 1
    FINISH: Literal[2] = 2


class TypingStatus:
    TYPING: Literal[1] = 1
    CANCEL: Literal[2] = 2


# ---------------------------------------------------------------------------
# Nested types
# ---------------------------------------------------------------------------
class BaseInfo(BaseModel):
    """Common request metadata attached to every CGI request."""

    channel_version: str | None = None


class TextItem(BaseModel):
    text: str | None = None


class CDNMedia(BaseModel):
    """CDN media reference."""

    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None
    full_url: str | None = None


class ImageItem(BaseModel):
    """Image item with CDN references."""

    media: CDNMedia | None = None
    thumb_media: CDNMedia | None = None
    aeskey: str | None = None
    url: str | None = None
    mid_size: int | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None
    hd_size: int | None = None


class VoiceItem(BaseModel):
    """Voice item with CDN reference and metadata."""

    media: CDNMedia | None = None
    encode_type: int | None = None
    bits_per_sample: int | None = None
    sample_rate: int | None = None
    playtime: int | None = None
    text: str | None = None  # 语音转文字内容


class FileItem(BaseModel):
    """File attachment item."""

    media: CDNMedia | None = None
    file_name: str | None = None
    md5: str | None = None
    len: str | None = None


class VideoItem(BaseModel):
    """Video item with CDN reference."""

    media: CDNMedia | None = None
    video_size: int | None = None
    play_length: int | None = None
    video_md5: str | None = None
    thumb_media: CDNMedia | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None


class RefMessage(BaseModel):
    """Quoted/referenced message."""

    message_item: Optional["MessageItem"] = None
    title: str | None = None  # 摘要


class MessageItem(BaseModel):
    """Unified message item."""

    type: int | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    is_completed: bool | None = None
    msg_id: str | None = None
    ref_msg: RefMessage | None = None
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    voice_item: VoiceItem | None = None
    file_item: FileItem | None = None
    video_item: VideoItem | None = None


class WeixinMessage(BaseModel):
    """Unified message (proto: WeixinMessage)."""

    seq: int | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    to_user_id: str | None = None
    client_id: str | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    delete_time_ms: int | None = None
    session_id: str | None = None
    group_id: str | None = None
    message_type: int | None = None
    message_state: int | None = None
    item_list: list[MessageItem] | None = None
    context_token: str | None = None


# ---------------------------------------------------------------------------
# API request/response types
# ---------------------------------------------------------------------------
class GetUpdatesReq(BaseModel):
    """getUpdates request."""

    get_updates_buf: str | None = None  # Full context buf cached locally


class GetUpdatesResp(BaseModel):
    """getUpdates response."""

    ret: int | None = None
    errcode: int | None = None
    errmsg: str | None = None
    msgs: list[WeixinMessage] | None = None
    get_updates_buf: str | None = None
    longpolling_timeout_ms: int | None = None


class SendMessageReq(BaseModel):
    """SendMessage request — wraps a single WeixinMessage."""

    msg: WeixinMessage | None = None


class GetUploadUrlReq(BaseModel):
    """Request for pre-signed CDN upload URL."""

    filekey: str | None = None
    media_type: int | None = None
    to_user_id: str | None = None
    rawsize: int | None = None  # 原文件明文大小
    rawfilemd5: str | None = None  # 原文件明文 MD5
    filesize: int | None = None  # 原文件密文大小（AES-128-ECB 加密后）
    thumb_rawsize: int | None = None
    thumb_rawfilemd5: str | None = None
    thumb_filesize: int | None = None
    no_need_thumb: bool | None = None
    aeskey: str | None = None


class GetUploadUrlResp(BaseModel):
    """Response with pre-signed CDN upload URL."""

    upload_param: str | None = None
    thumb_upload_param: str | None = None
    upload_full_url: str | None = None


class SendTypingReq(BaseModel):
    """Send typing indicator request."""

    ilink_user_id: str | None = None
    typing_ticket: str | None = None
    status: int | None = None  # 1=typing (default), 2=cancel typing


class SendTypingResp(BaseModel):
    """Send typing response."""

    ret: int | None = None
    errmsg: str | None = None


class GetConfigResp(BaseModel):
    """GetConfig response: bot config including typing_ticket."""

    ret: int | None = None
    errmsg: str | None = None
    typing_ticket: str | None = None  # Base64-encoded typing ticket


# Workaround for forward references
RefMessage.model_rebuild()
