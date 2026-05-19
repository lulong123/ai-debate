import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class ModelRoute(BaseModel):
    """Single model routing entry for a specific agent role."""

    role: str  # default, moderator, debater, scorer, data_clerk
    model: str  # LiteLLM model string (e.g. "openai/gpt-4o")
    api_key: str = ""  # Resolved API key ($ENV_VAR already replaced)
    base_url: str = ""  # Resolved base URL


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM Configuration (global defaults, used as fallback)
    llm_model: str = "openai/glm-5.1"
    llm_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
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

    # Chain-of-Thought (two-pass thinking)
    enable_cot: bool = True  # Can be disabled via ENABLE_COT=false

    # Data Clerk (optional, legacy — now overridden by config.yaml)
    data_clerk_model: str = ""  # Uses default LLM model if empty

    # StatMuse (optional supplementary data source for sports/finance)
    statmuse_enabled: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- YAML model routing (loaded once at startup) ---
    _model_routes: dict[str, ModelRoute] = {}

    def load_model_config(self) -> None:
        """Load model routing from config.yaml (next to this package).

        Falls back to global .env settings if no YAML file exists.
        """
        config_path = Path(__file__).parent.parent / "config.yaml"
        if not config_path.exists():
            logger.info("No config.yaml found, using global .env LLM settings")
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load config.yaml: %s", e)
            return

        for entry in raw.get("models", []):
            role = entry.get("role", "default")
            api_key = self._resolve_env(entry.get("api_key", ""))
            base_url = self._resolve_env(entry.get("base_url", ""))
            route = ModelRoute(
                role=role,
                model=entry.get("model", ""),
                api_key=api_key,
                base_url=base_url,
            )
            # First un-commented entry per role wins
            if role not in self._model_routes:
                self._model_routes[role] = route
                logger.info(
                    "Model route: %s → %s (base_url=%s)",
                    role,
                    route.model,
                    route.base_url or "(default)",
                )

    def get_model_config(self, role: str) -> tuple[str, str, str]:
        """Resolve (model, api_key, base_url) for a given agent role.

        Priority: role-specific → default → global .env
        """
        # 1. Exact role match from YAML
        if role in self._model_routes:
            r = self._model_routes[role]
            return (
                r.model or self.llm_model,
                r.api_key or self.llm_api_key,
                r.base_url or self.llm_base_url,
            )
        # 2. Fallback to "default" role from YAML
        if "default" in self._model_routes:
            r = self._model_routes["default"]
            return (
                r.model or self.llm_model,
                r.api_key or self.llm_api_key,
                r.base_url or self.llm_base_url,
            )
        # 3. Final fallback to global .env settings
        return self.llm_model, self.llm_api_key, self.llm_base_url

    @staticmethod
    def _resolve_env(value: str) -> str:
        """Replace $VAR_NAME with os.environ[VAR_NAME]."""
        if isinstance(value, str) and value.startswith("$"):
            return os.environ.get(value[1:], "")
        return value


# Global singleton
settings = Settings()
settings.load_model_config()
