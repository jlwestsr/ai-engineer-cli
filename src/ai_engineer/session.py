"""Persist metadata-only JSONL session events under ``.aie/sessions/``."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4


class SessionStore:
    """Append metadata events to a new owner-readable JSONL session file."""

    def __init__(self, workspace: Path, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid4().hex[:12]
        directory = workspace / ".aie" / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.session_id}.jsonl"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.path = path
        self._file: TextIO = os.fdopen(descriptor, "w", encoding="utf-8")
        self.write(
            {
                "type": "session_start",
                "session_id": self.session_id,
                "workspace": str(workspace.resolve()),
            }
        )

    def write(self, event: dict[str, Any]) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **event,
        }
        self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self.write({"type": "session_end", "session_id": self.session_id})
            self._file.close()
