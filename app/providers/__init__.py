"""Tracking data providers and the resolver that selects one at runtime."""
from __future__ import annotations

from ..config import Settings
from .base import ProviderError, TrackingProvider
from .mock import MockProvider
from .official import OfficialProvider
from .scrape import ScrapeProvider
from .web import WebProvider

__all__ = [
    "TrackingProvider",
    "ProviderError",
    "MockProvider",
    "OfficialProvider",
    "ScrapeProvider",
    "WebProvider",
    "build_chain",
]


def build_chain(settings: Settings) -> list[TrackingProvider]:
    """Ordered list of providers to try for a lookup.

    The first provider that returns a confident result wins; later providers act
    as fallbacks. In `auto` mode we always end with the mock provider so the UI
    never shows an empty screen.
    """
    mode = settings.mode
    mock = MockProvider(settings)

    if mode == "mock":
        return [mock]
    if mode == "official":
        return [OfficialProvider(settings)]
    if mode == "web":
        return [WebProvider(settings)]
    if mode == "scrape":
        # Real data via headless browser; demo fallback if it fails/blocks.
        return [ScrapeProvider(settings), mock]

    # auto
    chain: list[TrackingProvider] = []
    if settings.has_official_key:
        chain.append(OfficialProvider(settings))
    else:
        chain.append(WebProvider(settings))
    chain.append(mock)
    return chain
