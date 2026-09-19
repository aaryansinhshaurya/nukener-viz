"""Opaque one-time links. Only a digest is stored in the database."""

import hashlib
import secrets


def new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
