"""processOneMessage — message dispatch to ACP core pipeline.

This module integrates the WeChat message pipeline with ACP protocol,
using session_update() to send responses back to the ACP host.
"""

from src.api.client import send_typing
from src.api.types import MessageItemType, TypingStatus
from src.media_ops.media_download import download_media_from_item
from src.messaging.inbound import (
    WeixinInboundMediaOpts,
    get_context_token_from_msg_context,
    set_context_token,
    weixin_message_to_msg_context,
)
from src.runtime import resolve_weixin_channel_runtime
from src.util.logger import logger
from src.util.async_helpers import maybe_await


async def process_one_message(
    full: "WeixinMessage",  # type: ignore
    deps: dict,
) -> None:
    """Process a single inbound message: route → download media → dispatch reply."""
    channel_runtime = await resolve_weixin_channel_runtime(deps.get("channel_runtime"))
    if not deps.get("channel_runtime"):
        logger.info("processOneMessage: channelRuntime is undefined, using resolved runtime")

    from src.auth.pairing import is_user_allowed
    sender_allowed = is_user_allowed(full.from_user_id or "", deps["account_id"])

    # Build media opts by downloading/decrypting media
    media_opts: WeixinInboundMediaOpts = {}

    # Find first downloadable media item (IMAGE > VIDEO > FILE > VOICE)
    main_media_item = _find_downloadable_media_item(full.item_list)
    ref_media_item = None if main_media_item else _find_ref_media_item(full.item_list)
    media_item = main_media_item or ref_media_item

    if media_item:
        label = "ref" if ref_media_item else "inbound"
        try:
            downloaded = await download_media_from_item(media_item, {
                "cdn_base_url": deps["cdn_base_url"],
                "save_media": channel_runtime.get("media", {}).get("save_media_buffer"),
                "log": deps.get("log", lambda x: None),
                "err_log": deps.get("err_log", lambda x: None),
                "label": label,
            })
            # Map downloaded dict to WeixinInboundMediaOpts
            media_opts = WeixinInboundMediaOpts(
                decrypted_pic_path=downloaded.get("decryptedPicPath"),
                decrypted_voice_path=downloaded.get("decryptedVoicePath"),
                voice_media_type=downloaded.get("voiceMediaType"),
                decrypted_file_path=downloaded.get("decryptedFilePath"),
                file_media_type=downloaded.get("fileMediaType"),
                decrypted_video_path=downloaded.get("decryptedVideoPath"),
            )
        except Exception as err:
            logger.error(f"media download failed: {err}")

    ctx = weixin_message_to_msg_context(full, deps["account_id"], media_opts)
    ctx.command_body = ctx.body.strip() if ctx.body else ""
    route = await maybe_await(
        channel_runtime.get("routing", {}).get("resolve_agent_route"),
        {
            "account_id": deps["account_id"],
            "from_user_id": full.from_user_id or "",
            "body": ctx.body,
        },
    )
    if route and getattr(route, "session_key", None):
        ctx.session_key = route.session_key

    finalized_ctx = await maybe_await(
        channel_runtime.get("reply", {}).get("finalize_inbound_context"),
        ctx,
    ) or ctx

    if ctx.session_key:
        await maybe_await(
            channel_runtime.get("session", {}).get("record_inbound_session"),
            {
                "session_key": ctx.session_key,
                "account_id": deps["account_id"],
                "ctx": finalized_ctx,
                "metadata": {
                    "context_token": full.context_token,
                    "message_timestamp": full.create_time_ms,
                },
            },
        )

    context_token = get_context_token_from_msg_context(ctx)
    if context_token:
        set_context_token(deps["account_id"], full.from_user_id or "", context_token)

    # Framework command authorization using allowFrom list
    command_authorized = "authorized" if sender_allowed else "unauthorized"
    ctx.command_authorized = command_authorized

    if not sender_allowed:
        logger.info(
            f"processOneMessage: user {full.from_user_id} not in allowFrom list, skipping ACP prompt"
        )
        return

    try:
        from src.agent.agent import (
            WeixinAcpContext,
            get_active_session_id,
            get_agent_connection,
            set_weixin_context,
        )
    except Exception as err:
        logger.debug(f"[agent] agent bridge unavailable: {err}")
        get_active_session_id = None  # type: ignore
        get_agent_connection = None  # type: ignore
        set_weixin_context = None  # type: ignore
        WeixinAcpContext = None  # type: ignore

    conn = get_agent_connection() if get_agent_connection else None
    session_id = get_active_session_id() if get_active_session_id else None
    reply_runtime = channel_runtime.get("reply", {})
    if conn and session_id and set_weixin_context and WeixinAcpContext:
        try:
            set_weixin_context(
                WeixinAcpContext(
                    to_user_id=full.from_user_id or "",
                    base_url=deps["base_url"],
                    token=deps.get("token"),
                    context_token=context_token,
                    typing_ticket=deps.get("typing_ticket"),
                    cdn_base_url=deps.get("cdn_base_url"),
                    account_id=deps["account_id"],
                )
            )
            prompt_text = finalized_ctx.body.strip()
            if prompt_text:
                logger.debug(
                    f"[agent] prompting ACP for to={full.from_user_id or ''} "
                    f"context={bool(context_token)} typing_ticket={bool(deps.get('typing_ticket'))}"
                )

                async def _send_typing_status(status: int) -> None:
                    typing_ticket = deps.get("typing_ticket")
                    if not typing_ticket:
                        return
                    try:
                        await send_typing(
                            base_url=deps["base_url"],
                            token=deps.get("token"),
                            ilink_user_id=full.from_user_id or "",
                            typing_ticket=typing_ticket,
                            status=status,
                        )
                    except Exception as err:
                        logger.debug(f"[weixin] typing status send failed: {err}")

                dispatcher_bundle = await maybe_await(
                    reply_runtime.get("create_reply_dispatcher_with_typing"),
                    {
                        "typing_callbacks": {
                            "start": lambda: _send_typing_status(TypingStatus.TYPING),
                            "keepalive": lambda: _send_typing_status(TypingStatus.TYPING),
                            "stop": lambda: _send_typing_status(TypingStatus.CANCEL),
                        },
                        "typing_keepalive_interval_ms": 3000,
                    },
                ) or {}
                dispatcher = dispatcher_bundle.get("dispatcher")
                reply_options = dispatcher_bundle.get("reply_options") or {}
                mark_dispatch_idle = dispatcher_bundle.get("mark_dispatch_idle") or (lambda: None)
                try:
                    await maybe_await(
                        reply_runtime.get("with_reply_dispatcher"),
                        {
                            "dispatcher": dispatcher,
                            "run": lambda: maybe_await(
                                reply_runtime.get("dispatch_reply_from_config"),
                                {
                                    "ctx": finalized_ctx,
                                    "cfg": deps.get("config", {}),
                                    "dispatcher": dispatcher,
                                    "reply_options": reply_options,
                                },
                            ),
                        },
                    )
                finally:
                    mark_dispatch_idle()
            else:
                logger.debug("[agent] skipping ACP prompt for empty inbound text body")
        except Exception as e:
            logger.debug(f"[agent] prompt failed: {e}")

    logger.debug(f"processOneMessage: stub complete for message from={full.from_user_id}")


def _find_downloadable_media_item(item_list: list | None) -> "MessageItem | None":  # type: ignore
    """Find first downloadable media item in item_list."""
    if not item_list:
        return None

    def has_download(m: "MessageItem") -> bool:  # type: ignore
        media = getattr(m, ("image_item" if m.type == MessageItemType.IMAGE else
                            "video_item" if m.type == MessageItemType.VIDEO else
                            "file_item" if m.type == MessageItemType.FILE else
                            "voice_item").replace("_item", "_item"), None)
        if not media:
            return False
        return bool(getattr(media, "encrypt_query_param", None) or getattr(media, "full_url", None))

    for mtype, attr in [
        (MessageItemType.IMAGE, "image_item"),
        (MessageItemType.VIDEO, "video_item"),
        (MessageItemType.FILE, "file_item"),
        (MessageItemType.VOICE, "voice_item"),
    ]:
        item = next((i for i in item_list if i.type == mtype and has_download(i)), None)
        if item:
            return item
    return None


def _find_ref_media_item(item_list: list | None) -> "MessageItem | None":  # type: ignore
    """Find media item referenced via quoted message."""
    if not item_list:
        return None
    for item in item_list:
        if item.type == MessageItemType.TEXT and item.ref_msg and item.ref_msg.message_item:
            ref_item = item.ref_msg.message_item
            if ref_item.type in (MessageItemType.IMAGE, MessageItemType.VIDEO, MessageItemType.FILE, MessageItemType.VOICE):
                return ref_item
    return None
