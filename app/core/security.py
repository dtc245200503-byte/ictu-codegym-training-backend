from datetime import datetime, timedelta, timezone

import hashlib
import secrets
import string

import jwt
from pwdlib import PasswordHash

from app.core.config import settings


from functools import lru_cache

password_hash = PasswordHash.recommended()


def generate_temporary_password(length: int = 20) -> str:
    """Generate a unique, strong password for newly provisioned accounts."""
    if length < 12:
        raise ValueError("Temporary passwords must be at least 12 characters long.")
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in candidate)
            and any(char.isupper() for char in candidate)
            and any(char.isdigit() for char in candidate)
            and any(char in "!@#$%*-_" for char in candidate)
        ):
            return candidate


@lru_cache(maxsize=128)
def hash_password(password: str) -> str:
    """Hash a plaintext password before storing it."""
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its stored hash."""
    return password_hash.verify(password, hashed_password)


def create_access_token(
    subject: str,
    expires_minutes: int | None = None,
) -> str:
    """Create a signed JWT access token."""
    duration = (
        expires_minutes
        if expires_minutes is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=duration),
        "type": "access",
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
def create_refresh_token() -> str:
    """Create a cryptographically secure opaque refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hash of a refresh token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def create_password_reset_token() -> str:
    """Create a cryptographically secure password reset token."""
    return secrets.token_urlsafe(32)


def hash_password_reset_token(token: str) -> str:
    """Return the SHA-256 hash of a password reset token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
