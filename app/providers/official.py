"""Official 17track API provider (https://api.17track.net).

Flow: ensure the number is registered, then pull real-time tracking info.
Requires a free API key in SEVENTEENTRACK_API_KEY.
"""
from __future__ import annotations

from typing import Optional

import httpx

from ..config import Settings
from ..normalize import normalize_official
from .base import ProviderError, TrackingProvider


class OfficialProvider(TrackingProvider):
    name = "official"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._base = settings.seventeentrack_api_base.rstrip("/")
        self._key = settings.seventeentrack_api_key.strip()

    def _headers(self) -> dict:
        return {"17token": self._key, "Content-Type": "application/json"}

    async def track(self, number: str, carrier: Optional[int] = None) -> Optional[TrackResult]:
        if not self._key:
            raise ProviderError("No 17track API key configured")

        payload = [{"number": number, **({"carrier": carrier} if carrier else {})}]
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                # Register (idempotent; "already registered" is fine).
                await client.post(f"{self._base}/register", headers=self._headers(), json=payload)
                resp = await client.post(
                    f"{self._base}/gettrackinfo", headers=self._headers(), json=payload
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"17track request failed: {exc}") from exc

        if resp.status_code == 401:
            raise ProviderError("17track rejected the API key (401)")
        if resp.status_code == 429:
            raise ProviderError("17track rate limit reached (429)")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("17track returned a non-JSON response") from exc

        if body.get("code") not in (0, None):
            raise ProviderError(f"17track API error code {body.get('code')}")

        data = body.get("data") or {}
        accepted = data.get("accepted") or []
        if not accepted:
            rejected = data.get("rejected") or []
            if rejected:
                err = (rejected[0].get("error") or {}).get("message") or "rejected"
                raise ProviderError(f"17track rejected the number: {err}")
            return None  # registered but no info yet → let caller fall back

        result = normalize_official(number, carrier, accepted[0], self.settings.include_raw)
        # Brand-new registrations often have no events yet; treat as "no data".
        if result.status == "NotFound" and not result.events:
            return None
        return result
