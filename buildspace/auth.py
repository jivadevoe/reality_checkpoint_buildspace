# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
"""Shared-secret auth between the server and whatever drives it.

The server owns one random token. Agents send it as `Authorization: Bearer
<token>`; browsers pair once (paste the token, or open the pairing link) and
get a cookie derived from it. Rotating the token logs out every browser and
agent at once.

Where the token comes from, in order:
  1. BUILDSPACE_TOKEN in the environment
  2. the file at BUILDSPACE_TOKEN_FILE (default ~/.buildspace/token),
     created with mode 600 on first server start

An agent on the same machine as the server reads the same file, so it needs
no setup. An agent on another machine needs BUILDSPACE_TOKEN set to the
server's token: that hand-off is the pairing step, done by the user.
"""
import hashlib
import hmac
import os
import secrets
from pathlib import Path

COOKIE_NAME = "buildspace_session"


def token_path() -> Path:
    raw = os.environ.get("BUILDSPACE_TOKEN_FILE")
    return Path(raw).expanduser() if raw else Path.home() / ".buildspace" / "token"


def read_token() -> str | None:
    """The token if one is configured, without creating it (client side)."""
    env = os.environ.get("BUILDSPACE_TOKEN", "").strip()
    if env:
        return env
    try:
        return token_path().read_text().strip() or None
    except OSError:
        return None


def load_or_create_token() -> str:
    """The server's token, generating and saving one on first run."""
    existing = read_token()
    if existing:
        return existing
    return _write_new_token()


def rotate_token() -> str:
    """Replace the saved token. The running server keeps the old one until
    it restarts."""
    if os.environ.get("BUILDSPACE_TOKEN", "").strip():
        raise RuntimeError("BUILDSPACE_TOKEN is set in the environment; change it there instead")
    try:
        token_path().unlink()
    except FileNotFoundError:
        pass
    return _write_new_token()


def _write_new_token() -> str:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    # O_EXCL + 0600 so the file is never readable by others, even briefly.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def session_value(token: str) -> str:
    """Cookie value for a paired browser. Derived from the token rather than
    the token itself, so the raw secret never sits in browser storage."""
    return hmac.new(token.encode(), b"buildspace-browser-session", hashlib.sha256).hexdigest()


def check_bearer(header: str | None, token: str) -> bool:
    if not header or not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.encode())


def check_cookie(value: str | None, token: str) -> bool:
    if not value:
        return False
    return hmac.compare_digest(value.encode(), session_value(token).encode())
