"""QR code renderer for terminal display."""
from io import StringIO

import qrcode


async def qr_code_to_terminal(qrcode_content: str, scale: int = 2) -> str | None:
    """Render QR code content as terminal ASCII output.

    Args:
        qrcode_content: Raw text content to encode as a QR code
        scale: Unused legacy argument kept for compatibility

    Returns:
        ASCII art string, or None if rendering fails
    """
    del scale

    if not qrcode_content:
        return None

    try:
        qr = qrcode.QRCode(
            version=1,
            box_size=1,
            border=1,
        )
        qr.add_data(qrcode_content)
        qr.make(fit=True)

        output = StringIO()
        qr.print_ascii(out=output, invert=True)
        return output.getvalue().rstrip()
    except Exception:
        return None
