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

    # Optional — enables `beacon_refresh_chatbot_rag` in `local` mode.
    # Path to a local checkout of the ai-chatbot repo, and OpenAI creds
    # to run its embedding build script.
    ai_chatbot_path: str = Field(default="", alias="AI_CHATBOT_PATH")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # Optional — enables `beacon_refresh_chatbot_rag` in `webhook` mode.
    # Vercel Deploy Hook URL for the ai-chatbot production branch. When
    # set (and mode="webhook"), the refresh tool POSTs to this URL and
    # Vercel handles the rebuild via ADR-021 phase 1 wiring — no local
    # Bun runtime, no local git checkout, no local push credentials.
    #
    # Get the URL from Vercel dashboard → project settings → Git → Deploy
    # Hooks. Store as secret — the URL IS the auth token; anyone who
    # knows it can trigger deploys.
    vercel_deploy_hook_url: str = Field(default="", alias="VERCEL_DEPLOY_HOOK_URL")

    # RAG refresh mode used by the listener when it fires a rebuild.
    # "local"   — the shell-out-to-bun-and-git-push path (default,
    #             preserves existing behavior).
    # "webhook" — POST to vercel_deploy_hook_url (ADR-021 target state).
    # The MCP tool itself exposes both modes and defaults to `local`;
    # the listener honors this override so cutover can happen without
    # touching listener code.
    rag_refresh_mode: str = Field(default="local", alias="RAG_REFRESH_MODE")

    # Only used by `server.listener`, not by the MCP server. Direct
    # Postgres URL to Beacon so the listener can `LISTEN rag_stale`
    # for event-driven RAG rebuilds. Must include the driver — this
    # is the raw asyncpg URL (`postgresql://user:pw@host:5432/db`),
    # NOT the SQLAlchemy `postgresql+asyncpg://` variant.
    beacon_database_url: str = Field(default="", alias="BEACON_DATABASE_URL")

    # Debounce window for the RAG listener. After a NOTIFY arrives,
    # wait this many seconds before triggering a rebuild; any further
    # NOTIFYs in the window reset the timer. Ten seconds is enough to
    # coalesce a burst of edits (batch project imports, resume-import
    # backfills) into a single rebuild without adding noticeable lag.
    rag_debounce_seconds: float = Field(default=10.0, alias="RAG_DEBOUNCE_SECONDS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def base(self) -> str:
        return self.beacon_api_url.rstrip("/")


settings = Settings()
