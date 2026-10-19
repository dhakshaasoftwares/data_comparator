"""Password hashing, JWT tokens and encryption of connection secrets."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .settings import get_settings

_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(check.hex(), digest)


def create_token(user_id: int, org_id: int) -> str:
    s = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {"sub": str(user_id), "org": org_id, "iat": now, "exp": now + dt.timedelta(minutes=s.token_ttl_minutes)}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])


def _fernet() -> Fernet:
    s = get_settings()
    key = s.encryption_key or base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt_secrets(values: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(values).encode()).decode() if values else ""


def decrypt_secrets(token: str) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise ValueError("Cannot decrypt connection secrets (encryption key changed?)") from exc
