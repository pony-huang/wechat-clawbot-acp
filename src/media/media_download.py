"""Download and decrypt media from Weixin MessageItem (IMAGE/VOICE/FILE/VIDEO)."""
from src.api.types import MessageItemType
from src.cdn.pic_decrypt import download_and_decrypt_buffer, download_plain_cdn_buffer
from src.media.mime import get_mime_from_filename
from src.media.silk_transcode import silk_to_wav
from src.util.logger import logger

WEIXIN_MEDIA_MAX_BYTES = 100 * 1024 * 1024


# Type alias for save_media function signature
SaveMediaFn = lambda buffer, content_type=None, subdir=None, max_bytes=None, original_filename=None: {
    "path": str
}


async def download_media_from_item(
    item: "MessageItem",
    deps: dict,
) -> dict:
    """Download and decrypt media from a single MessageItem.

    Returns populated dict with decryptedPicPath, decryptedVoicePath, etc.
    """
    cdn_base_url = deps["cdn_base_url"]
    save_media = deps["save_media"]
    log = deps["log"]
    err_log = deps["err_log"]
    label = deps["label"]

    result: dict = {}

    item_type = item.type

    if item_type == MessageItemType.IMAGE:
        img = item.image_item
        if not (img and (img.media and img.media.encrypt_query_param) and not img.media.full_url):
            if not img or (not img.media or not img.media.encrypt_query_param) and not img.media.full_url:
                return result

        aes_key_base64: str | None = None
        if img.aeskey:
            import base64

            aes_key_base64 = base64.b64encode(bytes.fromhex(img.aeskey)).decode()
        elif img.media and img.media.aes_key:
            aes_key_base64 = img.media.aes_key

        try:
            if aes_key_base64:
                buf = await download_and_decrypt_buffer(
                    img.media.encrypt_query_param or "",
                    aes_key_base64,
                    cdn_base_url,
                    f"{label} image",
                    img.media.full_url,
                )
            else:
                buf = await download_plain_cdn_buffer(
                    img.media.encrypt_query_param or "",
                    cdn_base_url,
                    f"{label} image-plain",
                    img.media.full_url,
                )

            saved = await save_media(buf, None, "inbound", WEIXIN_MEDIA_MAX_BYTES)
            result["decryptedPicPath"] = saved["path"]
            logger.debug(f"{label} image saved: {saved['path']}")
        except Exception as err:
            logger.error(f"{label} image download/decrypt failed: {err}")
            err_log(f"weixin {label} image download/decrypt failed: {err}")

    elif item_type == MessageItemType.VOICE:
        voice = item.voice_item
        if not (
            voice
            and voice.media
            and (voice.media.encrypt_query_param or voice.media.full_url)
            and voice.media.aes_key
        ):
            return result

        try:
            silk_buf = await download_and_decrypt_buffer(
                voice.media.encrypt_query_param or "",
                voice.media.aes_key,
                cdn_base_url,
                f"{label} voice",
                voice.media.full_url,
            )
            logger.debug(f"{label} voice: decrypted {len(silk_buf)} bytes, attempting silk transcode")

            wav_buf = await silk_to_wav(silk_buf)
            if wav_buf:
                saved = await save_media(wav_buf, "audio/wav", "inbound", WEIXIN_MEDIA_MAX_BYTES)
                result["decryptedVoicePath"] = saved["path"]
                result["voiceMediaType"] = "audio/wav"
                logger.debug(f"{label} voice: saved WAV to {saved['path']}")
            else:
                saved = await save_media(silk_buf, "audio/silk", "inbound", WEIXIN_MEDIA_MAX_BYTES)
                result["decryptedVoicePath"] = saved["path"]
                result["voiceMediaType"] = "audio/silk"
                logger.debug(f"{label} voice: silk transcode unavailable, saved raw SILK")
        except Exception as err:
            logger.error(f"{label} voice download/transcode failed: {err}")
            err_log(f"weixin {label} voice download/transcode failed: {err}")

    elif item_type == MessageItemType.FILE:
        file_item = item.file_item
        if not (
            file_item
            and file_item.media
            and (file_item.media.encrypt_query_param or file_item.media.full_url)
            and file_item.media.aes_key
        ):
            return result

        try:
            buf = await download_and_decrypt_buffer(
                file_item.media.encrypt_query_param or "",
                file_item.media.aes_key,
                cdn_base_url,
                f"{label} file",
                file_item.media.full_url,
            )
            mime = get_mime_from_filename(file_item.file_name or "file.bin")
            saved = await save_media(buf, mime, "inbound", WEIXIN_MEDIA_MAX_BYTES, file_item.file_name)
            result["decryptedFilePath"] = saved["path"]
            result["fileMediaType"] = mime
            logger.debug(f"{label} file: saved to {saved['path']} mime={mime}")
        except Exception as err:
            logger.error(f"{label} file download failed: {err}")
            err_log(f"weixin {label} file download failed: {err}")

    elif item_type == MessageItemType.VIDEO:
        video_item = item.video_item
        if not (
            video_item
            and video_item.media
            and (video_item.media.encrypt_query_param or video_item.media.full_url)
            and video_item.media.aes_key
        ):
            return result

        try:
            buf = await download_and_decrypt_buffer(
                video_item.media.encrypt_query_param or "",
                video_item.media.aes_key,
                cdn_base_url,
                f"{label} video",
                video_item.media.full_url,
            )
            saved = await save_media(buf, "video/mp4", "inbound", WEIXIN_MEDIA_MAX_BYTES)
            result["decryptedVideoPath"] = saved["path"]
            logger.debug(f"{label} video: saved to {saved['path']}")
        except Exception as err:
            logger.error(f"{label} video download failed: {err}")
            err_log(f"weixin {label} video download failed: {err}")

    return result
