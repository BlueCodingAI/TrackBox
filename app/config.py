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
    # When True, fall back to demo data if a scrape fails. Default False so
    # scrape mode returns real data or an honest "not found" — never fake data.
    scrape_fallback_mock: bool = False
    # Optional outbound proxy for the browser (e.g. http://user:pass@host:port).
    # A residential proxy greatly improves Cloudflare clearance from a VPS.
    scrape_proxy: str = ""

    # ── captcha / Cloudflare solver (used by scrape mode when blocked) ────────
    solver_provider: str = ""   # "twocaptcha" (or empty to disable)
    solver_api_key: str = ""
    solver_timeout: float = 180.0

    @property
    def solver_enabled(self) -> bool:
        return bool(self.solver_provider.strip() and self.solver_api_key.strip())

    @property
    def mode(self) -> str:
        return (self.provider_mode or "auto").strip().lower()

    @property
    def has_official_key(self) -> bool:
        return bool(self.seventeentrack_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
