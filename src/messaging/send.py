"""Weixin outbound message sending (text + media)."""
from src.api.client import send_message as api_send_message
from src.api.types import MessageItem, MessageItemType, MessageState, MessageType, SendMessageReq
from src.util.logger import logger
from src.util.random import generate_id


async def send_message_weixin(
    to: str,
    text: str,
    opts: dict,
) -> dict:
    """Send a plain text message downstream."""
    context_token = opts.get("context_token")
    if not context_token:
        logger.warn(f"[message] sendMessageWeixin: contextToken missing for to={to}, sending without context")

    client_id = generate_id("wechat-acp")
    req = _build_text_message_req(to, text, context_token, client_id)

    try:
        await api_send_message(
            base_url=opts["base_url"],
            token=opts.get("token"),
            body=req,
        )
    except Exception as err:
        logger.error(f"[message] sendMessageWeixin: failed to={to} clientId={client_id} err={err}")
        raise err

    logger.info(f"[message] sendMessageWeixin: success to={to} clientId={client_id} text_len={len(text)}")
    return {"message_id": client_id}


def _build_text_message_req(
    to: str,
    text: str,
    context_token: str | None,
    client_id: str,
) -> SendMessageReq:
    """Build a SendMessageReq containing a single text message."""
    item_list: list[MessageItem] = (
        [MessageItem(type=MessageItemType.TEXT, text_item={"text": text})]
        if text
        else []
    )
    return SendMessageReq(
        msg={
            "from_user_id": "",
            "to_user_id": to,
            "client_id": client_id,
            "message_type": MessageType.BOT,
            "message_state": MessageState.FINISH,
            "item_list": item_list if item_list else None,
            "context_token": context_token,
        }
    )


async def _send_media_items(
    to: str,
    text: str,
    media_item: MessageItem,
    opts: dict,
    label: str,
) -> dict:
    """Send one or more MessageItems downstream (text caption + media item)."""
    context_token = opts.get("context_token")

    items: list[MessageItem] = []
    if text:
        items.append(MessageItem(type=MessageItemType.TEXT, text_item={"text": text}))
    items.append(media_item)

    last_client_id = ""
    for item in items:
        last_client_id = generate_id("wechat-acp")
        req = SendMessageReq(
            msg={
                "from_user_id": "",
                "to_user_id": to,
                "client_id": last_client_id,
                "message_type": MessageType.BOT,
                "message_state": MessageState.FINISH,
                "item_list": [item],
                "context_token": context_token,
            }
        )
        try:
            await api_send_message(
                base_url=opts["base_url"],
                token=opts.get("token"),
                body=req,
            )
        except Exception as err:
            logger.error(f"{label}: failed to={to} clientId={last_client_id} err={err}")
            raise err

    logger.info(f"[message] {label}: success to={to} clientId={last_client_id}")
    return {"message_id": last_client_id}


async def send_image_message_weixin(
    to: str,
    text: str,
    uploaded: "UploadedFileInfo",  # type: ignore
    opts: dict,
) -> dict:
    """Send an image message downstream using a previously uploaded file."""
    context_token = opts.get("context_token")
    if not context_token:
        logger.warn(f"[message] sendImageMessageWeixin: contextToken missing for to={to}")

    import base64

    image_item = MessageItem(
        type=MessageItemType.IMAGE,
        image_item={
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": base64.b64encode(bytes.fromhex(uploaded.aeskey)).decode(),
                "encrypt_type": 1,
            },
            "mid_size": uploaded.file_size_ciphertext,
        },
    )
    return await _send_media_items(to, text, image_item, opts, "sendImageMessageWeixin")


async def send_video_message_weixin(
    to: str,
    text: str,
    uploaded: "UploadedFileInfo",  # type: ignore
    opts: dict,
) -> dict:
    """Send a video message downstream using a previously uploaded file."""
    context_token = opts.get("context_token")
    if not context_token:
        logger.warn(f"[message] sendVideoMessageWeixin: contextToken missing for to={to}")

    import base64

    video_item = MessageItem(
        type=MessageItemType.VIDEO,
        video_item={
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": base64.b64encode(bytes.fromhex(uploaded.aeskey)).decode(),
                "encrypt_type": 1,
            },
            "video_size": uploaded.file_size_ciphertext,
        },
    )
    return await _send_media_items(to, text, video_item, opts, "sendVideoMessageWeixin")


async def send_file_message_weixin(
    to: str,
    text: str,
    file_name: str,
    uploaded: "UploadedFileInfo",  # type: ignore
    opts: dict,
) -> dict:
    """Send a file attachment downstream using a previously uploaded file."""
    context_token = opts.get("context_token")
    if not context_token:
        logger.warn(f"[message] sendFileMessageWeixin: contextToken missing for to={to}")

    import base64

    file_item = MessageItem(
        type=MessageItemType.FILE,
        file_item={
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": base64.b64encode(bytes.fromhex(uploaded.aeskey)).decode(),
                "encrypt_type": 1,
            },
            "file_name": file_name,
            "len": str(uploaded.file_size),
        },
    )
    return await _send_media_items(to, text, file_item, opts, "sendFileMessageWeixin")


from src.cdn.upload import UploadedFileInfo
