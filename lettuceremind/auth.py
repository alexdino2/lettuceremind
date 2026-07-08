"""Local user accounts: registration, login, and per-user pantries.

Accounts are local to this machine — there is no server involved.
Passwords are never stored: ``users.json`` keeps a salted
PBKDF2-HMAC-SHA256 hash, and the session file holds only a random token
that logging out revokes.

Logging in gives *every* feature its own pantry: scan, add, list,
expiring, remove, clear, and deals all read and write
``~/.lettuceremind/users/<name>/pantry.json`` while a session is active
(see :func:`lettuceremind.store.default_store_path`). Without a login the
app keeps using the shared pantry, so pre-account setups work unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lettuceremind.paths import base_dir

PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD_LENGTH = 8
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


class AuthError(Exception):
    """A registration or login problem the user can fix."""


def users_path() -> Path:
    return base_dir() / "users.json"


def session_path() -> Path:
    return base_dir() / "session.json"


def user_pantry_path(username: str) -> Path:
    return base_dir() / "users" / username / "pantry.json"


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # credentials & sessions are private to the user
    except OSError:
        pass
    tmp.replace(path)


def _load_users() -> dict:
    return _load_json(users_path()).get("users", {})


def _save_users(users: dict) -> None:
    _write_json(users_path(), {"users": users})


def _hash_password(password: str, salt: bytes, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return dk.hex()


def _canonical_username(username: str) -> str:
    name = username.strip().lower()
    if not _USERNAME_RE.match(name):
        raise AuthError(
            "invalid username: use 3-32 characters — letters, digits, "
            "'.', '_' or '-', starting with a letter or digit"
        )
    return name


def register(username: str, password: str) -> str:
    """Create an account and start a session for it.

    Returns the canonical (lowercased) username.
    """
    name = _canonical_username(username)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    users = _load_users()
    if name in users:
        raise AuthError(f"username {name!r} is already taken")
    salt = secrets.token_bytes(16)
    users[name] = {
        "salt": salt.hex(),
        "hash": _hash_password(password, salt, PBKDF2_ITERATIONS),
        "iterations": PBKDF2_ITERATIONS,
        "created_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _start_session(name, users)
    return name


def login(username: str, password: str) -> str:
    """Verify credentials and start a session. Returns the username."""
    name = username.strip().lower()
    users = _load_users()
    record = users.get(name)
    # Hash against a dummy record when the user is unknown so both failure
    # modes take the same time and produce the same message.
    salt = bytes.fromhex(record["salt"]) if record else b"\x00" * 16
    iterations = int(record["iterations"]) if record else PBKDF2_ITERATIONS
    expected = record["hash"] if record else _hash_password("", salt, iterations)
    supplied = _hash_password(password, salt, iterations)
    if record is None or not hmac.compare_digest(supplied, expected):
        raise AuthError("invalid username or password")
    _start_session(name, users)
    return name


def logout() -> Optional[str]:
    """End the active session. Returns the username that was logged out."""
    session = _load_json(session_path())
    name = session.get("username")
    try:
        session_path().unlink()
    except OSError:
        pass
    users = _load_users()
    if isinstance(name, str) and name in users:
        if users[name].pop("session_token", None) is not None:
            _save_users(users)
        return name
    return None


def current_user() -> Optional[str]:
    """The username of the active session, or None.

    The session file's token must match the one recorded at login; a stale
    or tampered session file therefore never authenticates.
    """
    session = _load_json(session_path())
    name, token = session.get("username"), session.get("token")
    if not isinstance(name, str) or not isinstance(token, str):
        return None
    record = _load_users().get(name)
    if not isinstance(record, dict):
        return None
    stored = record.get("session_token")
    if not isinstance(stored, str) or not hmac.compare_digest(stored, token):
        return None
    return name


def _start_session(name: str, users: dict) -> None:
    token = secrets.token_hex(16)
    users[name]["session_token"] = token
    _save_users(users)
    _write_json(session_path(), {
        "username": name,
        "token": token,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
