"""Фабрика LLM — единая точка создания chat-моделей для агента и его пост-обработки."""

from __future__ import annotations

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from blocksnet_agent.config import get_settings

_active_model: str | None = None


def set_active_model(model: str | None) -> None:
    """Переопределяет модель для текущего процесса (сбрасывает кэш фабрики)."""
    global _active_model
    _active_model = model
    get_chat_model.cache_clear()


def get_default_model_id() -> str:
    return _active_model or get_settings().model


@lru_cache(maxsize=4)
def get_chat_model(
    model_id: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> BaseChatModel:
    """Возвращает LangChain chat-модель. Кэшируется по (model_id, temperature, max_tokens)."""
    settings = get_settings()
    mid = model_id or _active_model or settings.model
    effort = (settings.reasoning_effort or "").strip() or None
    return ChatOpenAI(
        model=mid,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=settings.api_key,
        base_url=settings.chat_url,
        # OpenRouter-style body field. ChatOpenAI's own ``reasoning=`` kwarg would switch the
        # client to the Responses API instead of Chat Completions.
        extra_body={"reasoning": {"effort": effort}} if effort else None,
    )
