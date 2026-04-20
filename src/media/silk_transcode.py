"""SILK audio transcoding to WAV via silk-wasm.

This module provides SILK audio transcoding to WAV format.
If silk-wasm is unavailable, returns None and callers fall back to raw SILK.
"""
import logging

SILK_SAMPLE_RATE = 24_000

logger = logging.getLogger(__name__)


async def silk_to_wav(silk_buf: bytes) -> bytes | None:
    """Try to transcode SILK audio buffer to WAV using silk-wasm.

    Returns WAV Buffer on success, or None if silk-wasm is unavailable or decoding fails.
    """
    try:
        import silk_wasm

        # silk-wasm decode returns {data: Uint8Array, duration: number}
        result = await asyncio.to_thread(silk_wasm.decode, silk_buf, SILK_SAMPLE_RATE)
        logger.debug(
            f"silkToWav: decoded duration={result.duration}ms pcmBytes={len(result.data)}"
        )

        wav = _pcm_bytes_to_wav(result.data, SILK_SAMPLE_RATE)
        logger.debug(f"silkToWav: WAV size={len(wav)}")
        return wav
    except Exception as err:
        logger.warning(f"silkToWav: transcode failed, will use raw silk err={err}")
        return None


import asyncio


def _pcm_bytes_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM S16LE bytes in a WAV container (mono, 16-bit, LE)."""
    pcm_bytes = len(pcm)
    total_size = 44 + pcm_bytes
    buf = bytearray(total_size)
    offset = 0

    # RIFF header
    buf[offset : offset + 4] = b"RIFF"
    offset += 4
    buf[offset:offset + 4] = (total_size - 8).to_bytes(4, "little")
    offset += 4
    buf[offset : offset + 4] = b"WAVE"
    offset += 4

    # fmt chunk
    buf[offset : offset + 4] = b"fmt "
    offset += 4
    buf[offset : offset + 4] = (16).to_bytes(4, "little")
    offset += 4
    buf[offset : offset + 2] = (1).to_bytes(2, "little")  # PCM
    offset += 2
    buf[offset : offset + 2] = (1).to_bytes(2, "little")  # mono
    offset += 2
    buf[offset : offset + 4] = sample_rate.to_bytes(4, "little")
    offset += 4
    buf[offset : offset + 4] = (sample_rate * 2).to_bytes(4, "little")  # byte rate
    offset += 4
    buf[offset : offset + 2] = (2).to_bytes(2, "little")  # block align
    offset += 2
    buf[offset : offset + 2] = (16).to_bytes(2, "little")  # bits per sample
    offset += 2

    # data chunk
    buf[offset : offset + 4] = b"data"
    offset += 4
    buf[offset : offset + 4] = pcm_bytes.to_bytes(4, "little")
    offset += 4

    buf[offset : offset + pcm_bytes] = pcm
    return bytes(buf)
