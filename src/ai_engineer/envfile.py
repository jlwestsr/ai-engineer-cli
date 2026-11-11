"""Minimal ``.env`` loader (no third-party dependency)."""

from __future__ import annotations

import os
from pathlib import Path

from ai_engineer.envpolicy import is_denied_env_key


def _unescape_double_quoted(value: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            if nxt == "n":
                pieces.append("\n")
            elif nxt == "t":
                pieces.append("\t")
            elif nxt == '"':
                pieces.append('"')
            elif nxt == "\\":
                pieces.append("\\")
            else:
                pieces.append(nxt)
            index += 2
            continue
        pieces.append(char)
        index += 1
    return "".join(pieces)


def _parse_env_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key or any(char.isspace() for char in key):
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = _unescape_double_quoted(value)
    else:
        # Unquoted values: drop trailing inline comments.
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        elif value.startswith("#"):
            value = ""
    return key, value


def load_env_file(path: Path, *, override: bool = False) -> bool:
    """Load ``KEY=VALUE`` pairs into ``os.environ``.

    Returns True if the file existed and was read. Existing variables are kept
    unless ``override`` is True. Supports optional ``export `` prefixes, simple
    quoted values, and unquoted trailing `` #`` comments. Process-control and
    credential-shaped keys are skipped.
    """
    if not path.is_file():
        return False
    # Bound the loader; workspace tool reads use a similar order of magnitude.
    data = path.read_bytes()
    if len(data) > 64 * 1024:
        raise ValueError(f".env file exceeds 64 KiB: {path}")
    for raw in data.decode("utf-8").splitlines():
        parsed = _parse_env_line(raw)
        if parsed is None:
            continue
        key, value = parsed
        if is_denied_env_key(key, for_envfile=True):
            continue
        if override or key not in os.environ:
            os.environ[key] = value
    return True
