"""
Draftly – symmetric encryption for stored OAuth tokens using Fernet.

Usage:
    from app.utils.crypto import encrypt_token, decrypt_token

    cipher = encrypt_token("raw_token")
    plain  = decrypt_token(cipher)
"""
import base64
import logging
from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Lazily initialise Fernet; gracefully degrade in dev when key is missing.
def _get_fernet() -> Fernet | None:
    key = settings.encryption_key
    if not key:
        logger.warning(
            "ENCRYPTION_KEY not set – tokens will be stored unencrypted. "
            "Set a Fernet key in production!"
        )
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        logger.error("Invalid ENCRYPTION_KEY: %s", exc)
        return None


def encrypt_token(plaintext: str) -> str:
    """Encrypt *plaintext* and return a base64-safe cipher string."""
    fernet = _get_fernet()
    if fernet is None:
        # Fallback: base64-encode (NOT secure – only for local dev)
        return base64.b64encode(plaintext.encode()).decode()
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a previously encrypted token string."""
    fernet = _get_fernet()
    if fernet is None:
        return base64.b64decode(ciphertext.encode()).decode()
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Token decryption failed – token may be corrupted or key changed.")
