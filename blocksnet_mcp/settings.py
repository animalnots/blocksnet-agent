from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()


class MCPSettings(BaseSettings):
    """Runtime settings for the local stdio MCP server.

    a2a/03: LLM-поля (chat_url/api_key/model) сделаны необязательными —
    MCP-сервер должен подниматься без них, потому что инструменты каталога
    работают на чистых данных (load_*, compute_*), LLM нужен только для
    ``analyze_urban_question``, который сам проверяет настройки и возвращает
    ``LLM_NOT_CONFIGURED`` при их отсутствии.
    """

    # a2a/03: LLM-поля — None по умолчанию. Если None — analyze_urban_question
    # вернёт структурированный failed-ответ, остальные инструменты работают.
    chat_url: str | None = Field(default=None, validation_alias="CHAT_URL")
    api_key: str | None = Field(default=None, validation_alias="API_KEY")
    model: str | None = Field(default=None, validation_alias="MODEL")
    data_dir: Path = Field(default=PROJECT_ROOT / "data", validation_alias="DATA_DIR")
    output_dir: Path = Field(default=PROJECT_ROOT / "outputs", validation_alias="OUTPUT_DIR")
    max_iterations: int = Field(default=24, validation_alias="MAX_ITERATIONS")
    # P0.2: серверный дедлайн (сек). По истечении — статус partial, а не failed.
    deadline_sec: int = Field(default=480, validation_alias="DEADLINE_SEC")
    # P0.2: интервал progress-уведомлений (сек). 0 — отключить.
    progress_interval_sec: float = Field(default=10.0, validation_alias="PROGRESS_INTERVAL_SEC")
    # a2a/02: TTL и лимит сессий MCP. GeoDataFrame кварталов + матрица доступности
    # занимают сотни мегабайт — сессии надо выметать по TTL, иначе процесс
    # разрастётся до OOM за пару часов активной работы.
    session_ttl_sec: float = Field(default=1800.0, validation_alias="SESSION_TTL_SEC")
    max_sessions: int = Field(default=8, validation_alias="MAX_SESSIONS")
    # a2a/06: выставлять ли legacy ``analyze_urban_question`` (LLM-агент) как
    # MCP-инструмент. По умолчанию True, чтобы существующие клиенты не сломались.
    # Выключить через ``ENABLE_AGENT_TOOL=false`` для raw-tools-only режима.
    enable_agent_tool: bool = Field(default=True, validation_alias="ENABLE_AGENT_TOOL")
    # a2a/06: bearer auth для HTTP-транспорта MCP (stdio auth НЕ применяется —
    # транспорт локальный). При ``AUTH_ENABLED=true`` — обязателен
    # ``MAS_BEARER_TOKEN`` (fail-fast на старте).
    auth_enabled: bool = Field(default=False, validation_alias="AUTH_ENABLED")
    mas_bearer_token: str | None = Field(default=None, validation_alias="MAS_BEARER_TOKEN")
    # Транспорт MCP-сервера: ``stdio`` (по умолчанию — локальные клиенты) или
    # ``http`` — streamable-http на ``HOST:PORT`` + ``MCP_PATH`` для сетевых
    # клиентов (Synapse). ``MCP_HOST``/``MCP_PORT`` приняты как синонимы, чтобы
    # не пересекаться с ``A2A_HOST``/``A2A_PORT`` агента в общем ``.env``.
    transport: str = Field(default="stdio", validation_alias="TRANSPORT")
    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("MCP_HOST", "HOST"))
    port: int = Field(default=8001, validation_alias=AliasChoices("MCP_PORT", "PORT"))
    mcp_path: str = Field(default="/mcp", validation_alias="MCP_PATH")

    model_config = {
        "populate_by_name": True,
        "env_file": PROJECT_ROOT / ".env",
        "extra": "ignore",
    }

    def model_post_init(self, _) -> None:
        self.data_dir = self.data_dir.expanduser()
        self.output_dir = self.output_dir.expanduser()
        if not self.data_dir.is_absolute():
            self.data_dir = PROJECT_ROOT / self.data_dir
        if not self.output_dir.is_absolute():
            self.output_dir = PROJECT_ROOT / self.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_mcp_settings() -> MCPSettings:
    return MCPSettings()


def reset_mcp_settings() -> None:
    """Сбрасывает lru_cache для get_mcp_settings(). Используется в тестах."""
    get_mcp_settings.cache_clear()
