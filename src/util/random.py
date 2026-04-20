"""Random ID generation utilities."""
import secrets
from datetime import datetime


def generate_id(prefix: str) -> str:
    """Generate a prefixed unique ID using timestamp + crypto random bytes.

    Format: {prefix}:{timestamp}-{8-char hex}
    """
    return f"{prefix}:{datetime.utcnow().timestamp()}-{secrets.token_hex(4)}"


def temp_file_name(prefix: str, ext: str) -> str:
    """Generate a temporary file name with random suffix.

    Format: {prefix}-{timestamp}-{8-char hex}{ext}
    """
    return f"{prefix}-{datetime.utcnow().timestamp()}-{secrets.token_hex(4)}{ext}"
