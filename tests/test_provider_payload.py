"""OpenAI-compatible request payload shape."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request

from ai_engineer.provider import OpenAICompatibleProvider


def test_empty_tools_omits_tool_fields(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def read(self, _n: int = -1) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}
            ).encode()

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: int = 0) -> FakeResponse:
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr("ai_engineer.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider("https://example.test/v1", "demo", "key")
    response = provider.complete([{"role": "user", "content": "hi"}], [])
    assert response.content == "ok"
    assert "tools" not in captured["body"]
    assert "tool_choice" not in captured["body"]


def test_nonempty_tools_include_tool_choice(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def read(self, _n: int = -1) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}
            ).encode()

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: int = 0) -> FakeResponse:
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr("ai_engineer.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider("https://example.test/v1", "demo", "key")
    tools = [
        {
            "type": "function",
            "function": {"name": "get_current_time", "description": "t", "parameters": {}},
        }
    ]
    provider.complete([{"role": "user", "content": "hi"}], tools)
    assert captured["body"]["tools"] == tools
    assert captured["body"]["tool_choice"] == "auto"
