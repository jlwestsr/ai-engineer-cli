"""Persist metadata-only JSONL session events under ``.aie/sessions/``."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO
from uuid import uuid4


class SessionStore:
    """Append metadata events to a new owner-readable JSONL session file."""

    def __init__(self, workspace: Path, session_id: str | None = None) -> None:
        requested = session_id or uuid4().hex[:12]
        directory = workspace / ".aie" / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{requested}.jsonl"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            self.session_id = requested
        except FileExistsError:
            # Explicit reuse or collision: mint a fresh id rather than truncate.
            self.session_id = f"{requested}-{uuid4().hex[:8]}"
            path = directory / f"{self.session_id}.jsonl"
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.path = path
        self._file: TextIO = os.fdopen(descriptor, "w", encoding="utf-8")
        self._closed = False
        self.write(
            {
                "type": "session_start",
                "session_id": self.session_id,
                "workspace": str(workspace.resolve()),
            }
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def write(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            **event,
        }
        self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._file.closed:
            try:
                self._file.write(
                    json.dumps(
                        {
                            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                            "type": "session_end",
                            "session_id": self.session_id,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                self._file.flush()
            finally:
                self._file.close()
