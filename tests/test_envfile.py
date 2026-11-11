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


def test_load_env_file_skips_control_keys(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "AIE_MODEL=ok",
                "PYTHONPATH=/evil",
                "LD_PRELOAD=/evil.so",
                "GIT_SSH_COMMAND=ssh -i /evil",
                "PATH=/evil/bin",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AIE_MODEL", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    assert load_env_file(path) is True
    assert os.environ["AIE_MODEL"] == "ok"
    assert "PYTHONPATH" not in os.environ
    assert "LD_PRELOAD" not in os.environ
    assert "GIT_SSH_COMMAND" not in os.environ
    assert os.environ["PATH"] == "/usr/bin"


def test_load_env_file_skips_loader_opts_but_keeps_provider_keys(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "AIE_MODEL=ok",
                "AIE_API_KEY=sk-parent",
                "NODE_OPTIONS=--require /evil",
                "PYTHONSTARTUP=/evil.py",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for key in ("AIE_MODEL", "AIE_API_KEY", "NODE_OPTIONS", "PYTHONSTARTUP"):
        monkeypatch.delenv(key, raising=False)
    assert load_env_file(path) is True
    assert os.environ["AIE_MODEL"] == "ok"
    assert os.environ["AIE_API_KEY"] == "sk-parent"
    assert "NODE_OPTIONS" not in os.environ
    assert "PYTHONSTARTUP" not in os.environ


def test_load_env_file_export_comments_and_escapes(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "export AIE_API_KEY=sk-from-export",
                "AIE_MODEL=gpt-4o-mini # comment",
                'GREETING="hello\\nworld"',
                "EMPTY=# whole value is a comment",
                "# ignored",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for key in ("AIE_API_KEY", "AIE_MODEL", "GREETING", "EMPTY"):
        monkeypatch.delenv(key, raising=False)
    assert load_env_file(path) is True
    assert os.environ["AIE_API_KEY"] == "sk-from-export"
    assert os.environ["AIE_MODEL"] == "gpt-4o-mini"
    assert os.environ["GREETING"] == "hello\nworld"
    assert os.environ["EMPTY"] == ""


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
