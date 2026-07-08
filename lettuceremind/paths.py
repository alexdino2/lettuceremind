"""Filesystem locations for LettuceRemind data.

Everything the app persists — the shared pantry, user accounts, sessions,
per-user pantries, and custom deal feeds — lives under a single base
directory: ``~/.lettuceremind`` by default, overridable with
``$LETTUCEREMIND_HOME`` (handy for tests and portable installs).
"""

from __future__ import annotations

import os
from pathlib import Path


def base_dir() -> Path:
    env = os.environ.get("LETTUCEREMIND_HOME")
    return Path(env) if env else Path.home() / ".lettuceremind"
