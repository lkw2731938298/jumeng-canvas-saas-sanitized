"""Password strength rules for registration (existing weak passwords may still login)."""

from __future__ import annotations

import re

from ..core.error_codes import ErrorCode
from ..core.errors import fail

PASSWORD_MIN_LENGTH = 8


def is_strong_password(password: str) -> bool:
    return (
        len(password or "") >= PASSWORD_MIN_LENGTH
        and bool(re.search(r"[A-Za-z]", password or ""))
        and bool(re.search(r"\d", password or ""))
        and bool(re.search(r"[^A-Za-z0-9]", password or ""))
    )


def require_strong_password(password: str) -> None:
    if not is_strong_password(password):
        fail(ErrorCode.WEAK_PASSWORD)
