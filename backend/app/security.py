"""面板自身的安全：密码哈希 + 会话令牌。

会话走 HttpOnly Cookie，令牌只签不发私钥。私钥属于被管节点，不在这里。
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import pbkdf2_hmac, sha256


def _b64(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    """PBKDF2-SHA256，避免引入 bcrypt 的 C 依赖。"""
    salt = secrets.token_bytes(16)
    dk = pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, dk = stored.split("$")
        if algo != "pbkdf2":
            return False
        calc = pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iters))
        return hmac.compare_digest(calc, _unb64(dk))
    except Exception:
        return False


def create_token(secret: str, subject: str, hours: int = 12) -> str:
    payload = {"sub": subject, "exp": int(time.time()) + hours * 3600,
               "iat": int(time.time()), "jti": secrets.token_hex(8)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), sha256).digest())
    return f"{body}.{sig}"


def decode_token(secret: str, token: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_b64(hmac.new(secret.encode(), body.encode(), sha256).digest()), sig):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload
