"""Mock and OpenAI-compatible chat-completions providers."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class Provider(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...


class MockProvider:
    """Deterministic scripted provider for offline demos and tests."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        if not messages:
            return ModelResponse("Mock mode received an empty transcript.")
        latest = messages[-1]
        if latest.get("role") == "tool":
            raw = latest.get("content", "")
            try:
                result = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError, json.JSONDecodeError):
                return ModelResponse("The tool returned a non-JSON or compacted payload.")
            if isinstance(result, dict) and "error" in result:
                return ModelResponse(f"The tool reported an error: {result['error']}")
            tool_call_id = latest.get("tool_call_id", "")
            if tool_call_id == "mock-time":
                return ModelResponse(f"The current local time is {result}.")
            if isinstance(tool_call_id, str) and tool_call_id.startswith("mock-engineering"):
                return ModelResponse(json.dumps(result, indent=2, ensure_ascii=False))
            return ModelResponse(f"The result is {result}.")

        available = {tool["function"]["name"] for tool in tools}
        prompt = ""
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                prompt = message["content"]
                break
        if not prompt:
            return ModelResponse(
                "Mock mode needs a user message. Try 'list files' or 'read AGENTS.md'."
            )
        engineering: tuple[str, dict[str, Any]] | None = None
        lowered = prompt.lower().strip()
        if lowered == "list files":
            engineering = ("list_files", {})
        elif lowered == "git status":
            engineering = ("git_status", {})
        elif lowered == "git diff":
            engineering = ("git_diff", {})
        elif lowered == "run tests":
            engineering = ("run_python_tests", {})
        elif lowered.startswith("read "):
            engineering = ("read_file", {"path": prompt[5:].strip()})
        elif lowered.startswith("search "):
            engineering = ("search_text", {"text": prompt[7:].strip()})
        elif lowered.startswith("symbols "):
            engineering = ("python_symbols", {"path": prompt[8:].strip()})
        if engineering is not None:
            name, arguments = engineering
            if name not in available:
                return ModelResponse(f"{name} is unavailable; its permission may not be enabled.")
            return ModelResponse(tool_calls=[ToolCall("mock-engineering", name, arguments)])
        if "time" in lowered and "get_current_time" in available:
            return ModelResponse(tool_calls=[ToolCall("mock-time", "get_current_time", {})])
        match = re.search(
            r"(?:calculate|what is)\s+([0-9\s()+\-*/%.]+)",
            prompt,
            re.IGNORECASE,
        )
        if match:
            return ModelResponse(
                "Mock mode has no calculator tool. "
                "Try 'list files', 'read AGENTS.md', 'search ToolRegistry', "
                "'symbols src/ai_engineer/cli.py', 'git status', or ask the time."
            )
        return ModelResponse(
            "Mock mode is a scripted example, not a language model. "
            "Try 'list files', 'read AGENTS.md', 'search WorkspaceTools', "
            "'symbols src/ai_engineer/workspace.py', 'git status', or 'What time is it?'."
        )


class OpenAICompatibleProvider:
    """HTTP chat-completions adapter for OpenAI-compatible endpoints."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = base_url.rstrip("/")
        if not self.endpoint.endswith("/chat/completions"):
            self.endpoint += "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.extra_headers = dict(extra_headers or {})

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        payload = json.dumps(body).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
            if len(data) > MAX_PROVIDER_RESPONSE_BYTES:
                raise RuntimeError("Provider response exceeds the 1 MiB limit")
            result = json.loads(data)
        except urllib.error.HTTPError as error:
            error.close()
            raise RuntimeError(f"Provider returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Could not reach model provider: {error.reason}") from error
        except (OSError, ValueError) as error:
            raise RuntimeError("Provider transport or JSON decoding failed") from error

        try:
            message = result["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("Expected a message object")
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise TypeError("Expected a tool_calls list")
            calls = [
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                )
                for call in raw_calls
            ]
            if any(
                not isinstance(call.arguments, dict)
                or not isinstance(call.id, str)
                or not call.id
                or not isinstance(call.name, str)
                or not call.name
                for call in calls
            ):
                raise ValueError("Invalid tool request")
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("Expected text content")
            return ModelResponse(content, calls)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError("Provider response shape was invalid") from error


def _first_env(*names: str) -> str | None:
    import os

    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def build_provider(name: str) -> Provider:
    import os

    if name == "mock":
        return MockProvider()
    if name == "openai":
        api_key = _first_env("AIE_API_KEY", "OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Set AIE_API_KEY (or OPENAI_API_KEY) for --provider openai")
        base_url = os.environ.get("AIE_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("AIE_MODEL", "gpt-4o-mini")
        return OpenAICompatibleProvider(base_url, model, api_key)
    if name == "openrouter":
        api_key = _first_env("AIE_API_KEY", "OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("Set OPENROUTER_API_KEY (or AIE_API_KEY) for --provider openrouter")
        base_url = os.environ.get("AIE_BASE_URL", "https://openrouter.ai/api/v1")
        model = os.environ.get("AIE_MODEL", "deepseek/deepseek-v4-pro")
        referer = os.environ.get(
            "AIE_HTTP_REFERER", "https://github.com/westailabs/ai-engineer-cli"
        )
        title = os.environ.get("AIE_APP_TITLE", "AI Engineer CLI")
        return OpenAICompatibleProvider(
            base_url,
            model,
            api_key,
            extra_headers={
                "HTTP-Referer": referer,
                "X-Title": title,
            },
        )
    raise ValueError(f"Unknown provider: {name}")
