"""Send error notice back to user (fire-and-forget)."""
from typing import Callable

from src.messaging.send import send_message_weixin
from src.util.logger import logger


async def send_weixin_error_notice(
    to: str,
    context_token: str | None,
    message: str,
    base_url: str,
    token: str | None = None,
    err_log: Callable[[str], None] | None = None,
) -> None:
    """Send a plain-text error notice back to the user.

    Fire-and-forget: errors are logged but never thrown.
    """
    if not context_token:
        logger.warn(f"sendWeixinErrorNotice: no contextToken for to={to}")

    try:
        await send_message_weixin(
            to=to,
            text=message,
            opts={"base_url": base_url, "token": token, "context_token": context_token},
        )
        logger.debug(f"sendWeixinErrorNotice: sent to={to}")
    except Exception as err:
        (err_log or logger.error)(f"[weixin] sendWeixinErrorNotice failed to={to}: {err}")
