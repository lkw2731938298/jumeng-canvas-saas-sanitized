from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt, JWTError

from .config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_jwt_token(user_id: str, phone: str = "", display_name: str = "") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
    }
    if phone:
        payload["phone"] = phone
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def verify_sso_token(token: str) -> dict | None:
    """Verify SSO token from main site. Returns payload or None."""
    return decode_jwt_token(token)
