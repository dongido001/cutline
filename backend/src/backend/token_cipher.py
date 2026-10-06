"""Encrypt social tokens at rest with a local key file."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path

from .settings import settings

PREFIX = "enc:v1:"


def _key_path() -> Path:
    path = settings.data_dir / ".token_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_key() -> bytes:
    configured = (settings.token_secret or "").strip()
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).digest()
    path = _key_path()
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) >= 32:
            return raw[:32]
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def encrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    if value.startswith(PREFIX):
        return value
    key = _load_key()
    nonce = secrets.token_bytes(16)
    raw = value.encode("utf-8")
    stream = hashlib.shake_256(key + nonce).digest(len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream, strict=True))
    digest = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    packed = base64.urlsafe_b64encode(nonce + digest + cipher).decode("ascii")
    return f"{PREFIX}{packed}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None or value == "" or not value.startswith(PREFIX):
        return value
    key = _load_key()
    packed = base64.urlsafe_b64decode(value[len(PREFIX):].encode("ascii"))
    nonce, digest, cipher = packed[:16], packed[16:48], packed[48:]
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(digest, expected):
        raise ValueError("Social token could not be decrypted")
    stream = hashlib.shake_256(key + nonce).digest(len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, stream, strict=True)).decode("utf-8")
