"""Unit tests for account_id module."""
import pytest

from src.util.account_id import (
    extract_raw_account_id,
    is_account_id_normalized,
    normalize_account_id_wechat as normalize_account_id,
)


class TestNormalizeAccountId:
    """Tests for normalize_account_id function."""

    def test_normalize_without_suffix(self):
        """Test normalizing account without suffix."""
        result = normalize_account_id("someaccountid")
        assert result == "someaccountid@im.wechat"

    def test_normalize_with_suffix(self):
        """Test normalizing account already with suffix."""
        result = normalize_account_id("someaccountid@im.wechat")
        assert result == "someaccountid@im.wechat"

    def test_normalize_empty_string(self):
        """Test normalizing empty string."""
        result = normalize_account_id("")
        assert result == ""

    def test_normalize_long_account_id(self):
        """Test normalizing long account ID."""
        result = normalize_account_id("a" * 100)
        assert result == ("a" * 100) + "@im.wechat"


class TestIsAccountIdNormalized:
    """Tests for is_account_id_normalized function."""

    def test_normalized_id_returns_true(self):
        """Test that normalized ID returns True."""
        result = is_account_id_normalized("someaccountid@im.wechat")
        assert result is True

    def test_unnormalized_id_returns_false(self):
        """Test that unnormalized ID returns False."""
        result = is_account_id_normalized("someaccountid")
        assert result is False

    def test_empty_string_returns_true(self):
        """Test that empty string returns True."""
        result = is_account_id_normalized("")
        assert result is True


class TestExtractRawAccountId:
    """Tests for extract_raw_account_id function."""

    def test_extract_from_normalized(self):
        """Test extracting raw ID from normalized."""
        result = extract_raw_account_id("someaccountid@im.wechat")
        assert result == "someaccountid"

    def test_extract_from_unnormalized(self):
        """Test extracting from unnormalized returns unchanged."""
        result = extract_raw_account_id("someaccountid")
        assert result == "someaccountid"

    def test_extract_empty_string(self):
        """Test extracting from empty string."""
        result = extract_raw_account_id("")
        assert result == ""


class TestRoundTrip:
    """Tests for round-trip normalization."""

    def test_normalize_then_extract(self):
        """Test normalize then extract returns original raw."""
        raw = "someaccountid"
        normalized = normalize_account_id(raw)
        extracted = extract_raw_account_id(normalized)
        assert extracted == raw

    def test_extract_then_normalize(self):
        """Test extract then normalize returns normalized."""
        normalized = "someaccountid@im.wechat"
        raw = extract_raw_account_id(normalized)
        re_normalized = normalize_account_id(raw)
        assert re_normalized == normalized