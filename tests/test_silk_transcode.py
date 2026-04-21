"""Unit tests for silk_transcode module."""
import pytest

from src.media_ops.silk_transcode import _pcm_bytes_to_wav


class TestPcmToWav:
    """Tests for PCM to WAV conversion."""

    def test_pcm_bytes_to_wav(self):
        """Test PCM to WAV conversion produces valid WAV header."""
        sample_rate = 24000
        pcm_data = b"\x00" * sample_rate * 2

        wav = _pcm_bytes_to_wav(pcm_data, sample_rate)

        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert wav[12:16] == b"fmt "
        assert wav[22:24] == b"\x01\x00"  # mono
        assert len(wav) > 44

    def test_wav_contains_pcm_data(self):
        """Test WAV file contains the original PCM data."""
        sample_rate = 24000
        pcm_data = b"\x01\x02\x03\x04" * 100

        wav = _pcm_bytes_to_wav(pcm_data, sample_rate)

        assert wav[44:] == pcm_data

    def test_wav_header_contains_riff_and_wave(self):
        """Test WAV header starts with RIFF and WAVE markers."""
        wav = _pcm_bytes_to_wav(b"\x00" * 100, 24000)

        assert wav[0:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert wav[12:16] == b"fmt "
        assert wav[36:40] == b"data"
