"""AES-128-ECB crypto utilities for CDN upload and download."""
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt buffer with AES-128-ECB (PKCS7 padding)."""
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(plaintext, AES.block_size))


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt buffer with AES-128-ECB (PKCS7 padding)."""
    cipher = AES.new(key, AES.MODE_ECB)
    return unpad(cipher.decrypt(ciphertext), AES.block_size)


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """Compute AES-128-ECB ciphertext size (PKCS7 padding to 16-byte boundary)."""
    return ((plaintext_size + 1) // 16) * 16
