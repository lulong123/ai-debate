from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM Configuration
    llm_model: str = "glm-5.1"
    llm_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/anthropic"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.7

    # Discussion defaults
    max_rounds: int = 3
    min_agents: int = 2
    max_agents: int = 6

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/roundtable.db"

    # Search (optional)
    search_provider: str = ""  # "zhipu" or "tavily"
    zhipu_search_api_key: str = ""
    tavily_api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
