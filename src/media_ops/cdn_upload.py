"""CDN HTTP upload with AES-128-ECB encryption and retry logic."""
import aiohttp

from src.media_ops.aes_ecb import encrypt_aes_ecb
from src.media_ops.cdn_url import build_cdn_upload_url
from src.util.logger import logger
from src.util.redact import redact_url

UPLOAD_MAX_RETRIES = 3


async def upload_buffer_to_cdn(
    buf: bytes,
    upload_full_url: str | None,
    upload_param: str | None,
    filekey: str,
    cdn_base_url: str,
    aeskey: bytes,
    label: str = "",
) -> dict:
    """Upload buffer to Weixin CDN with AES-128-ECB encryption.

    Returns {'download_param': str} with the x-encrypted-param header value.
    """
    ciphertext = encrypt_aes_ecb(buf, aeskey)
    trimmed_full = upload_full_url.strip() if upload_full_url else None
    if trimmed_full:
        cdn_url = trimmed_full
    elif upload_param:
        cdn_url = build_cdn_upload_url(cdn_base_url, upload_param, filekey)
    else:
        raise RuntimeError(f"{label}: CDN upload URL missing (need upload_full_url or upload_param)")

    logger.debug(f"{label}: CDN POST url={redact_url(cdn_url)} ciphertextSize={len(ciphertext)}")

    download_param: str | None = None
    last_error: Exception | None = None

    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    cdn_url,
                    data=ciphertext,
                    headers={"Content-Type": "application/octet-stream"},
                ) as res:
                    if res.status >= 400 and res.status < 500:
                        err_msg = res.headers.get("x-error-message") or await res.text()
                        logger.error(
                            f"{label}: CDN client error attempt={attempt} status={res.status} errMsg={err_msg}"
                        )
                        raise RuntimeError(f"CDN upload client error {res.status}: {err_msg}")
                    if res.status != 200:
                        err_msg = res.headers.get("x-error-message") or f"status {res.status}"
                        logger.error(
                            f"{label}: CDN server error attempt={attempt} status={res.status} errMsg={err_msg}"
                        )
                        raise RuntimeError(f"CDN upload server error: {err_msg}")

                    download_param = res.headers.get("x-encrypted-param") or None
                    if not download_param:
                        logger.error(f"{label}: CDN response missing x-encrypted-param header")
                        raise RuntimeError("CDN upload response missing x-encrypted-param header")

                    logger.debug(f"{label}: CDN upload success attempt={attempt}")
                    break
        except Exception as err:
            last_error = err
            if "client error" in str(err):
                raise err
            if attempt < UPLOAD_MAX_RETRIES:
                logger.error(f"{label}: attempt {attempt} failed, retrying... err={err}")
            else:
                logger.error(f"{label}: all {UPLOAD_MAX_RETRIES} attempts failed err={err}")

    if not download_param:
        raise last_error or RuntimeError(f"CDN upload failed after {UPLOAD_MAX_RETRIES} attempts")

    return {"download_param": download_param}
