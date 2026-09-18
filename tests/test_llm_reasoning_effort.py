"""``REASONING_EFFORT`` попадает в тело каждого LLM-запроса как ``reasoning.effort``."""

from __future__ import annotations

import pytest


def _chat_model_with_env(monkeypatch: pytest.MonkeyPatch, effort: str | None):
    monkeypatch.setenv("CHAT_URL", "http://llm.invalid/v1")
    monkeypatch.setenv("API_KEY", "test")
    if effort is None:
        monkeypatch.delenv("REASONING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("REASONING_EFFORT", effort)
    from blocksnet_agent.config import get_settings
    from blocksnet_agent.llm import get_chat_model

    get_settings.cache_clear()
    get_chat_model.cache_clear()
    try:
        return get_chat_model(temperature=0.0)
    finally:
        get_settings.cache_clear()
        get_chat_model.cache_clear()


def test_reasoning_effort_setting_is_sent_in_the_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _chat_model_with_env(monkeypatch, "low")
    assert model.extra_body == {"reasoning": {"effort": "low"}}
    # ChatOpenAI's own ``reasoning=`` would switch the client to the Responses API.
    assert model.reasoning is None


def test_no_reasoning_parameter_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _chat_model_with_env(monkeypatch, None)
    assert model.extra_body is None


def test_empty_reasoning_effort_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _chat_model_with_env(monkeypatch, "  ")
    assert model.extra_body is None
