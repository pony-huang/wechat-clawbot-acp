"""Send media file: upload local file and send as Weixin message."""
import os

from src.media_ops.upload import (
    upload_file_attachment_to_weixin,
    upload_file_to_weixin,
    upload_video_to_weixin,
)
from src.media_ops.mime import get_mime_from_filename
from src.messaging.send import (
    send_file_message_weixin,
    send_image_message_weixin,
    send_video_message_weixin,
)
from src.util.logger import logger


MEDIA_OUTBOUND_TEMP_DIR = os.path.join(
    os.path.expanduser("~"), ".wechat-acp", "tmp", "weixin", "media", "outbound-temp"
)


async def send_weixin_media_file(
    file_path: str,
    to: str,
    text: str,
    opts: dict,
    cdn_base_url: str,
) -> dict:
    """Upload a local file and send it as a Weixin message, routing by MIME type.

    video/* → uploadVideoToWeixin + sendVideoMessageWeixin
    image/* → uploadFileToWeixin + sendImageMessageWeixin
    else    → uploadFileAttachmentToWeixin + sendFileMessageWeixin
    """
    mime = get_mime_from_filename(file_path)
    upload_opts: dict = {"base_url": opts["base_url"], "token": opts.get("token")}

    if mime.startswith("video/"):
        logger.info(f"[weixin] sendWeixinMediaFile: uploading video filePath={file_path} to={to}")
        uploaded = await upload_video_to_weixin(
            file_path=file_path,
            to_user_id=to,
            opts=upload_opts,
            cdn_base_url=cdn_base_url,
        )
        logger.info(
            f"[weixin] sendWeixinMediaFile: video upload done filekey={uploaded.filekey} size={uploaded.file_size}"
        )
        return await send_video_message_weixin(to, text, uploaded, opts)

    if mime.startswith("image/"):
        logger.info(f"[weixin] sendWeixinMediaFile: uploading image filePath={file_path} to={to}")
        uploaded = await upload_file_to_weixin(
            file_path=file_path,
            to_user_id=to,
            opts=upload_opts,
            cdn_base_url=cdn_base_url,
        )
        logger.info(
            f"[weixin] sendWeixinMediaFile: image upload done filekey={uploaded.filekey} size={uploaded.file_size}"
        )
        return await send_image_message_weixin(to, text, uploaded, opts)

    # File attachment: pdf, doc, zip, etc.
    file_name = os.path.basename(file_path)
    logger.info(
        f"[weixin] sendWeixinMediaFile: uploading file attachment filePath={file_path} name={file_name} to={to}"
    )
    uploaded = await upload_file_attachment_to_weixin(
        file_path=file_path,
        file_name=file_name,
        to_user_id=to,
        opts=upload_opts,
        cdn_base_url=cdn_base_url,
    )
    logger.info(
        f"[weixin] sendWeixinMediaFile: file upload done filekey={uploaded.filekey} size={uploaded.file_size}"
    )
    return await send_file_message_weixin(to, text, file_name, uploaded, opts)
