"""Runtime configuration, loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # auto | mock | official | web
    provider_mode: str = "auto"

    seventeentrack_api_key: str = ""
    seventeentrack_api_base: str = "https://api.17track.net/track/v2.2"
    web_endpoint: str = "https://t.17track.net/restapi/track"

    request_timeout: float = 20.0
    include_raw: bool = False  # raw upstream payload is never sent to clients anyway

    # Interactive API docs (Swagger/ReDoc/OpenAPI). OFF by default so end users
    # can't see the API surface; set ENABLE_DOCS=true for local development.
    enable_docs: bool = False

    # ── headless-browser scrape provider (PROVIDER_MODE=scrape) ──────────────
    scrape_headless: bool = True
    scrape_timeout: float = 45.0
    scrape_browser_channel: str = "msedge"  # system Edge; falls back to chromium

    @property
    def mode(self) -> str:
        return (self.provider_mode or "auto").strip().lower()

    @property
    def has_official_key(self) -> bool:
        return bool(self.seventeentrack_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
