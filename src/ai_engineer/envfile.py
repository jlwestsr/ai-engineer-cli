"""Minimal ``.env`` loader (no third-party dependency)."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path, *, override: bool = False) -> bool:
    """Load ``KEY=VALUE`` pairs into ``os.environ``.

    Returns True if the file existed and was read. Existing variables are kept
    unless ``override`` is True. Quotes around values are stripped.
    """
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
    return True
