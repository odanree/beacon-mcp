"""Runtime config — env-driven so the same image can point at staging or prod."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Empty defaults force the operator to point at their own Beacon instance.
    # We don't want a real URL in the source so a forked clone can't
    # accidentally hit someone else's deployment.
    beacon_api_url: str = Field(default="", alias="BEACON_API_URL")
    beacon_jwt: str = Field(default="", alias="BEACON_JWT")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def base(self) -> str:
        return self.beacon_api_url.rstrip("/")


settings = Settings()
