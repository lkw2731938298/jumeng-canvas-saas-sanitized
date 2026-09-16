"""供应商密钥加解密单测。"""

from __future__ import annotations

import pytest

from app.common.utils.credential_crypto import api_key_hint, decrypt_secret, encrypt_secret


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "unit-test-key-material")
    from app.core.config import get_settings

    get_settings.cache_clear()

    plain = "sk-test-abcdef123456"
    blob = encrypt_secret(plain)
    assert blob
    assert decrypt_secret(blob) == plain


def test_api_key_hint_masks():
    assert api_key_hint("abcdefghijklmnop") == "mnop"
    assert api_key_hint("ab") == "ab"


def test_decrypt_invalid_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "unit-test-key-material")
    from app.core.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ValueError):
        decrypt_secret("not-valid-base64!!!")
