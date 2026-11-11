"""Env file and OpenRouter provider wiring."""

from __future__ import annotations

import os
from pathlib import Path

from ai_engineer.envfile import load_env_file
from ai_engineer.provider import OpenAICompatibleProvider, build_provider


def test_load_env_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text('OPENROUTER_API_KEY="sk-test"\nAIE_MODEL=demo\n', encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AIE_MODEL", raising=False)
    assert load_env_file(path) is True
    assert os.environ["OPENROUTER_API_KEY"] == "sk-test"
    assert os.environ["AIE_MODEL"] == "demo"


def test_openrouter_provider_defaults(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("AIE_API_KEY", raising=False)
    monkeypatch.delenv("AIE_BASE_URL", raising=False)
    monkeypatch.delenv("AIE_MODEL", raising=False)
    provider = build_provider("openrouter")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.endpoint == "https://openrouter.ai/api/v1/chat/completions"
    assert provider.model == "deepseek/deepseek-v4-pro"
    assert provider.extra_headers["X-Title"] == "AI Engineer CLI"
