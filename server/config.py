"""Runtime config — env-driven so the same image can point at staging or prod."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# When Claude Code launches the MCP server it sets its own CWD, so a
# relative .env path won't find ours. Resolve to the repo root absolutely.
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_PATH), extra="ignore")

    # Empty defaults force the operator to point at their own Beacon instance.
    # We don't want a real URL in the source so a forked clone can't
    # accidentally hit someone else's deployment.
    beacon_api_url: str = Field(default="", alias="BEACON_API_URL")
    beacon_jwt: str = Field(default="", alias="BEACON_JWT")

    # Optional — enables `beacon_refresh_chatbot_rag`. Path to a local
    # checkout of the ai-chatbot repo, and OpenAI creds to run its
    # embedding build script.
    ai_chatbot_path: str = Field(default="", alias="AI_CHATBOT_PATH")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def base(self) -> str:
        return self.beacon_api_url.rstrip("/")


settings = Settings()
