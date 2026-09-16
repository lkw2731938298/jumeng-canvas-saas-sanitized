"""供应商密钥 AES-256-GCM 加解密。"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ...core.config import get_settings

logger = logging.getLogger(__name__)

_NONCE_LEN = 12


def _derive_key_material() -> bytes:
    settings = get_settings()
    raw = (settings.credential_encryption_key or "").strip() or settings.jwt_secret
    if not raw:
        raise ValueError("credential_encryption_key or jwt_secret is required for credential crypto")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    """明文 → base64(nonce + ciphertext + tag)。"""
    if not plaintext:
        return ""
    key = _derive_key_material()
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(blob: str) -> str:
    """解密 encrypt_secret 产物；失败抛 ValueError。"""
    if not blob:
        return ""
    try:
        raw = base64.b64decode(blob)
        if len(raw) <= _NONCE_LEN:
            raise ValueError("ciphertext too short")
        nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
        return AESGCM(_derive_key_material()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception as exc:
        logger.warning("credential decrypt failed: %s", exc.__class__.__name__)
        raise ValueError("invalid encrypted credential") from exc


def api_key_hint(api_key: str) -> str:
    """仅保留后 4 位供管理端展示。"""
    key = (api_key or "").strip()
    if len(key) <= 4:
        return key
    return key[-4:]
