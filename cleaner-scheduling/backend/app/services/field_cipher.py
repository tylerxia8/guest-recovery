from __future__ import annotations

import hashlib
import hmac
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PREFIX = b"dev-hmac-stream-v1:"
_AEAD_PREFIX = b"aes256-gcm-v1:"


def protect_text(value: str, key_material: str) -> bytes:
    raw = value.encode("utf-8")
    nonce = os.urandom(12)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_AEAD_PREFIX).derive(key_material.encode())
    return _AEAD_PREFIX + nonce + AESGCM(key).encrypt(nonce, raw, _AEAD_PREFIX)


def reveal_text(blob: bytes, key_material: str) -> str:
    if blob.startswith(_AEAD_PREFIX):
        body = blob[len(_AEAD_PREFIX) :]
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_AEAD_PREFIX).derive(key_material.encode())
        return AESGCM(key).decrypt(body[:12], body[12:], _AEAD_PREFIX).decode("utf-8")
    if not blob.startswith(_PREFIX):
        raise ValueError("ciphertext version is not recognized")
    body = blob[len(_PREFIX) :]
    nonce, cipher = body[:12], body[12:]
    return _xor(cipher, key_material, nonce).decode("utf-8")


def read_private_key(filename: str) -> str:
    descriptor = os.open(Path(filename).expanduser(), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Field encryption key must be an owner-only regular file.")
        raw = stream.read(257)
    value = raw.strip()
    if len(raw) > 256 or not 43 <= len(value) <= 256 or not value.isascii() or any(char.isspace() for char in value):
        raise ValueError("Field encryption key is invalid.")
    return value


def _xor(data: bytes, key_material: str, nonce: bytes) -> bytes:
    key = key_material.encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    trimmed = bytes(stream[: len(data)])
    return bytes(left ^ right for left, right in zip(data, trimmed, strict=True))
