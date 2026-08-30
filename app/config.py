"""Configuration.

Environment variables are the only configuration input, and they are read in
exactly one place: ``Settings.from_env``. Nothing else in the codebase calls
``os.environ``, so the full configuration surface of the service is the list of
fields on this model.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Model IDs move over time; this is the default, and MODEL overrides it.
DEFAULT_MODEL = "claude-sonnet-4-5"

_PACKAGE_ROOT = Path(__file__).resolve().parent


class Settings(BaseModel):
    """Validated runtime configuration.

    ``anthropic_api_key`` is required for the agent to do anything useful, but
    it is deliberately *not* required to construct ``Settings``. A container
    that starts and reports "api_key: missing" on /health is far easier to
    diagnose than one that crash-loops before it can serve a request.
    """

    model_config = {"frozen": True}

    anthropic_api_key: str | None = None
    llm_client: Literal["anthropic", "scripted"] = "anthropic"
    model: str = DEFAULT_MODEL
    max_steps: int = Field(default=12, ge=1, le=50)
    tool_timeout_s: float = Field(default=30.0, gt=0)
    db_path: str = "runs.db"
    log_level: str = "INFO"
    corpus_dir: str = str(_PACKAGE_ROOT / "corpus" / "documents")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        src = os.environ if env is None else env
        raw = {
            "anthropic_api_key": src.get("ANTHROPIC_API_KEY") or None,
            "llm_client": (src.get("LLM_CLIENT") or "anthropic").strip().lower(),
            "model": src.get("MODEL") or DEFAULT_MODEL,
            "max_steps": src.get("MAX_STEPS") or 12,
            "tool_timeout_s": src.get("TOOL_TIMEOUT_S") or 30.0,
            "db_path": src.get("DB_PATH") or "runs.db",
            "log_level": (src.get("LOG_LEVEL") or "INFO").upper(),
            "corpus_dir": src.get("CORPUS_DIR") or str(_PACKAGE_ROOT / "corpus" / "documents"),
        }
        return cls.model_validate(raw)

    @property
    def api_key_present(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def api_key_required(self) -> bool:
        """Scripted mode makes no API calls, so a missing key is not a fault."""
        return self.llm_client == "anthropic"

    def require_api_key(self) -> str:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; the agent cannot make model calls."
            )
        return self.anthropic_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings_cache() -> None:
    """Test hook — lets a test mutate the environment and re-read it."""
    get_settings.cache_clear()
