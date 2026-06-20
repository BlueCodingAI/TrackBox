"""FastAPI application: tracking API + static frontend."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, carriers
from .config import get_settings
from .models import TrackResponse
from .providers import ProviderError, build_chain

logger = logging.getLogger("tracking_api")

_NUMBER_RE = re.compile(r"^[A-Za-z0-9\-]{5,50}$")
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# Swagger/ReDoc/OpenAPI are exposed only when ENABLE_DOCS=true (dev); otherwise
# they 404 so end users can't see the API surface.
_docs_on = get_settings().enable_docs
app = FastAPI(
    title="TrackBox",
    version=__version__,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _clean_number(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "")).strip()


async def _resolve(number: str, carrier: Optional[int]) -> TrackResponse:
    """Try providers in order; first confident result wins."""
    settings = get_settings()
    last_error: Optional[str] = None

    for provider in build_chain(settings):
        try:
            result = await provider.track(number, carrier)
        except ProviderError as exc:
            last_error = str(exc)
            logger.warning("provider %s failed: %s", provider.name, exc)
            continue
        except Exception as exc:  # defensive: never let one provider 500 the API
            last_error = f"{provider.name}: {exc}"
            logger.exception("provider %s crashed", provider.name)
            continue
        if result is not None:
            logger.info(
                "resolved %r via %s (source=%s, status=%s)",
                number, provider.name, result.source, result.status,
            )
            return TrackResponse(ok=True, result=result)

    # Keep internal/provider error detail in the logs only — never leak it to
    # unauthenticated clients in the response.
    if last_error:
        logger.info("all providers exhausted for %r; last error: %s", number, last_error)
    return TrackResponse(ok=False, error="No tracking information found for this number.")


@app.get("/api/health")
async def health() -> dict:
    # Intentionally minimal — does not expose provider mode or data source.
    return {"status": "ok"}


@app.get("/api/carriers")
async def list_carriers(
    q: str = Query("", description="Case-insensitive name filter"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    return {"carriers": carriers.search_carriers(q, limit)}


@app.get("/api/track", response_model=TrackResponse, response_model_exclude_none=True)
async def track(
    number: str = Query(..., description="Tracking number"),
    carrier: Optional[int] = Query(None, description="17track carrier code (optional)"),
) -> TrackResponse:
    num = _clean_number(number)
    if not _NUMBER_RE.match(num):
        return TrackResponse(
            ok=False,
            error="Invalid tracking number. Use 5–50 letters, digits or hyphens.",
        )
    return await _resolve(num, carrier)


# ── static frontend (registered last so /api/* takes precedence) ─────────────
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(_FRONTEND / "index.html")


if _FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
