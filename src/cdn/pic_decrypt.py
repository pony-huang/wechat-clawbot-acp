"""CDN download with AES-128-ECB decryption."""
import base64
from src.cdn.aes_ecb import decrypt_aes_ecb
from src.cdn.cdn_url import ENABLE_CDN_URL_FALLBACK, build_cdn_download_url
from src.util.logger import logger

# Use aiohttp for async fetch
import aiohttp


async def _fetch_bytes(url: str, label: str) -> bytes:
    """Fetch raw bytes from CDN URL."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as res:
                if not res.ok:
                    body = await res.text().catch(lambda _: "(unreadable)")
                    raise RuntimeError(
                        f"{label}: CDN download {res.status} {res.status_text} body={body}"
                    )
                return await res.read()
    except aiohttp.ClientError as err:
        cause = getattr(err, "cause", None) or getattr(err, "errno", None) or "(no cause)"
        raise RuntimeError(f"{label}: fetch network error url={url} err={err} cause={cause}")


def _parse_aes_key(aes_key_base64: str, label: str) -> bytes:
    """Parse CDNMedia.aes_key into a raw 16-byte AES key.

    Two encodings:
      - base64(raw 16 bytes)          → images
      - base64(hex string of 16 bytes) → file/voice/video
    """
    decoded = base64.b64decode(aes_key_base64)
    if decoded.length == 16:  # type: ignore
        return decoded
    # Hex-encoded key case
    decoded_str = decoded.decode("ascii")
    if len(decoded_str) == 32 and all(c in "0123456789abcdefABCDEF" for c in decoded_str):
        return bytes.fromhex(decoded_str)
    raise RuntimeError(
        f"{label}: aes_key must decode to 16 raw bytes or 32-char hex string, "
        f"got {len(decoded)} bytes"
    )


async def download_and_decrypt_buffer(
    encrypted_query_param: str,
    aes_key_base64: str,
    cdn_base_url: str,
    label: str,
    full_url: str | None = None,
) -> bytes:
    """Download and AES-128-ECB decrypt a CDN media file. Returns plaintext buffer."""
    key = _parse_aes_key(aes_key_base64, label)
    if full_url:
        url = full_url
    elif ENABLE_CDN_URL_FALLBACK:
        url = build_cdn_download_url(encrypted_query_param, cdn_base_url)
    else:
        raise RuntimeError(f"{label}: fullUrl is required (CDN URL fallback is disabled)")

    logger.debug(f"{label}: fetching url={url}")
    encrypted = await _fetch_bytes(url, label)
    logger.debug(f"{label}: downloaded {len(encrypted)} bytes, decrypting")
    decrypted = decrypt_aes_ecb(encrypted, key)
    logger.debug(f"{label}: decrypted {len(decrypted)} bytes")
    return decrypted


async def download_plain_cdn_buffer(
    encrypted_query_param: str,
    cdn_base_url: str,
    label: str,
    full_url: str | None = None,
) -> bytes:
    """Download plain (unencrypted) bytes from CDN."""
    if full_url:
        url = full_url
    elif ENABLE_CDN_URL_FALLBACK:
        url = build_cdn_download_url(encrypted_query_param, cdn_base_url)
    else:
        raise RuntimeError(f"{label}: fullUrl is required (CDN URL fallback is disabled)")

    logger.debug(f"{label}: fetching url={url}")
    return await _fetch_bytes(url, label)
