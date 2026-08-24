"""``OLLAMA_MAX_TOKENS`` override for the local-LLM (ollama) token cap.

The ollama path historically hardcoded 1024 max tokens per call. Structured
stages (diagnose/report) emit JSON payloads that truncation silently corrupts
— the JSON parse then fails and the investigation degrades to
"Unable to determine root cause". The env var lets operators raise the cap
without touching code; the default stays 1024.
"""

from __future__ import annotations

import pytest

from core.llm.client_builders import _resolve_ollama_max_tokens


def test_default_is_1024(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MAX_TOKENS", raising=False)
    assert _resolve_ollama_max_tokens() == 1024


def test_env_override_raises_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MAX_TOKENS", "4096")
    assert _resolve_ollama_max_tokens() == 4096


@pytest.mark.parametrize("bad", ["abc", "", "-5"])
def test_invalid_values_fall_back_to_default(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("OLLAMA_MAX_TOKENS", bad)
    assert _resolve_ollama_max_tokens() == 1024
