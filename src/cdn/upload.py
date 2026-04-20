"""CDN upload pipeline: read file → hash → AES encrypt → getUploadUrl → CDN POST."""
import asyncio
import hashlib
import os
import secrets
from dataclasses import dataclass

import aiohttp
import aiofiles

from src.api.client import get_upload_url
from src.cdn.aes_ecb import aes_ecb_padded_size
from src.cdn.cdn_upload import upload_buffer_to_cdn
from src.media.mime import get_extension_from_content_type_or_url
from src.util.logger import logger
from src.util.random import temp_file_name


@dataclass
class UploadedFileInfo:
    """Result of a successful CDN upload."""

    filekey: str
    download_encrypted_query_param: str
    aeskey: str  # hex-encoded
    file_size: int  # plaintext bytes
    file_size_ciphertext: int  # ciphertext bytes


async def download_remote_image_to_temp(url: str, dest_dir: str) -> str:
    """Download a remote media URL (image, video, file) to a local temp file.

    Returns the local file path; extension is inferred from Content-Type / URL.
    """
    logger.debug(f"downloadRemoteImageToTemp: fetching url={url}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as res:
            if not res.ok:
                msg = f"remote media download failed: {res.status} {res.status_text} url={url}"
                logger.error(f"downloadRemoteImageToTemp: {msg}")
                raise RuntimeError(msg)

            buf = await res.read()
            logger.debug(f"downloadRemoteImageToTemp: downloaded {len(buf)} bytes")

            content_type = res.headers.get("Content-Type")
            ext = get_extension_from_content_type_or_url(content_type, url)
            name = temp_file_name("weixin-remote", ext)
            file_path = f"{dest_dir}/{name}"

            os.makedirs(dest_dir, exist_ok=True)
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(buf)
            logger.debug(f"downloadRemoteImageToTemp: saved to {file_path} ext={ext}")
            return file_path


async def _upload_media_to_cdn(
    file_path: str,
    to_user_id: str,
    opts: dict,
    cdn_base_url: str,
    media_type: int,
    label: str,
) -> UploadedFileInfo:
    """Common upload pipeline: read file → hash → gen aeskey → getUploadUrl → uploadBufferToCdn."""
    async with aiofiles.open(file_path, "rb") as f:
        plaintext = await f.read()

    raw_size = len(plaintext)
    raw_file_md5 = hashlib.md5(plaintext).hexdigest()
    file_size = aes_ecb_padded_size(raw_size)
    filekey = secrets.token_hex(16)
    aeskey = secrets.token_bytes(16)

    logger.debug(
        f"{label}: file={file_path} rawsize={raw_size} filesize={file_size} md5={raw_file_md5} filekey={filekey}"
    )

    upload_resp = await get_upload_url(
        base_url=opts["base_url"],
        token=opts.get("token"),
        filekey=filekey,
        media_type=media_type,
        to_user_id=to_user_id,
        rawsize=raw_size,
        rawfilemd5=raw_file_md5,
        filesize=file_size,
        no_need_thumb=True,
        aeskey=aeskey.hex(),
    )

    upload_full_url = upload_resp.upload_full_url.strip() if upload_resp.upload_full_url else None
    upload_param = upload_resp.upload_param

    if not upload_full_url and not upload_param:
        raise RuntimeError(f"{label}: getUploadUrl returned no upload URL")

    result = await upload_buffer_to_cdn(
        buf=plaintext,
        upload_full_url=upload_full_url,
        upload_param=upload_param,
        filekey=filekey,
        cdn_base_url=cdn_base_url,
        aeskey=aeskey,
        label=f"{label}[orig filekey={filekey}]",
    )

    return UploadedFileInfo(
        filekey=filekey,
        download_encrypted_query_param=result["download_param"],
        aeskey=aeskey.hex(),
        file_size=raw_size,
        file_size_ciphertext=file_size,
    )


async def upload_file_to_weixin(
    file_path: str,
    to_user_id: str,
    opts: dict,
    cdn_base_url: str,
) -> UploadedFileInfo:
    """Upload a local image file to the Weixin CDN with AES-128-ECB encryption."""
    from src.api.types import UploadMediaType

    return await _upload_media_to_cdn(
        file_path=file_path,
        to_user_id=to_user_id,
        opts=opts,
        cdn_base_url=cdn_base_url,
        media_type=UploadMediaType.IMAGE,
        label="uploadFileToWeixin",
    )


async def upload_video_to_weixin(
    file_path: str,
    to_user_id: str,
    opts: dict,
    cdn_base_url: str,
) -> UploadedFileInfo:
    """Upload a local video file to the Weixin CDN."""
    from src.api.types import UploadMediaType

    return await _upload_media_to_cdn(
        file_path=file_path,
        to_user_id=to_user_id,
        opts=opts,
        cdn_base_url=cdn_base_url,
        media_type=UploadMediaType.VIDEO,
        label="uploadVideoToWeixin",
    )


async def upload_file_attachment_to_weixin(
    file_path: str,
    file_name: str,
    to_user_id: str,
    opts: dict,
    cdn_base_url: str,
) -> UploadedFileInfo:
    """Upload a local file attachment (non-image, non-video) to the Weixin CDN."""
    from src.api.types import UploadMediaType

    del file_name  # unused in upload pipeline
    return await _upload_media_to_cdn(
        file_path=file_path,
        to_user_id=to_user_id,
        opts=opts,
        cdn_base_url=cdn_base_url,
        media_type=UploadMediaType.FILE,
        label="uploadFileAttachmentToWeixin",
    )
