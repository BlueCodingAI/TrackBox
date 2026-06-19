"""Provider interface and shared errors."""
from __future__ import annotations

import abc
from typing import Optional

from ..config import Settings
from ..models import TrackResult


class ProviderError(Exception):
    """Raised when a provider cannot complete a lookup (network, auth, etc.)."""


class TrackingProvider(abc.ABC):
    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abc.abstractmethod
    async def track(self, number: str, carrier: Optional[int] = None) -> Optional[TrackResult]:
        """Look up a tracking number.

        Returns a `TrackResult` on success, or `None` if this provider has no
        confident answer (so the resolver can fall through to the next one).
        Raises `ProviderError` on hard failures (network/auth) that should be
        logged but still allow fallback.
        """
        raise NotImplementedError
