"""Session cookies and password checks.

Seeded demo accounts keep the original unsalted hash. New accounts use a
salted PBKDF2 hash. verify_password accepts both.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from app.config import SECRET

_PBKDF2_ROUNDS = 120_000


def hash_password(password: str) -> str:
    """Legacy hash used by the seeded demo accounts."""
    return hashlib.sha256(f"railseat:{password}".encode()).hexdigest()


def hash_password_new(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS).hex()
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("pbkdf2$"):
        try:
            _, rounds, salt, digest = stored.split("$", 3)
            got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(got, digest)
    return hmac.compare_digest(hash_password(password), stored)


def sign_session(user_id: int, role: str) -> str:
    exp = int(time.time()) + 14 * 24 * 3600
    msg = f"{user_id}.{role}.{exp}"
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{msg}.{sig}"


def read_session(token: str | None) -> dict | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    user_id, role, exp, sig = parts
    msg = f"{user_id}.{role}.{exp}"
    expect = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expect, sig):
        return None
    if int(exp) < time.time():
        return None
    return {"user_id": int(user_id), "role": role}
