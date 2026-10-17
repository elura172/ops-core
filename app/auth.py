import os
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week
MIN_SECRET_BYTES = 32
_LEGACY_FALLBACK_SECRET = "ops-core-jwt-secret-key-change-in-prod"


def get_jwt_secret_key() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "")
    if secret == _LEGACY_FALLBACK_SECRET or len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise RuntimeError(
            "JWT_SECRET_KEY must be configured with at least 32 bytes of secret material; "
            "generate one with `openssl rand -hex 32`"
        )
    return secret


def validate_jwt_configuration() -> None:
    """Fail application startup when JWT signing is not safely configured."""
    get_jwt_secret_key()


def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_jwt_secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_jwt_secret_key(), algorithms=[ALGORITHM])
