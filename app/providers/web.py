"""Best-effort public web provider (no API key).

This calls the same endpoint 17track's website uses. As of this writing the
anonymous endpoint is protected by anti-bot measures and typically rejects
programmatic requests, so this provider is written to fail *softly* — it returns
None (so `auto` mode falls back to demo data) rather than crashing. If/when the
endpoint becomes reachable again, parsing is already wired up.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import Settings
from ..models import TrackResult, coerce_status, status_label
from ..normalize import _addr, _carrier, _event, build_milestones
from .base import ProviderError, TrackingProvider

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class WebProvider(TrackingProvider):
    name = "web"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._url = settings.web_endpoint

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Origin": "https://t.17track.net",
            "Referer": "https://t.17track.net/en",
            "Accept": "application/json, text/plain, */*",
        }

    async def track(self, number: str, carrier: Optional[int] = None) -> Optional[TrackResult]:
        item: dict = {"num": number}
        if carrier:
            item["fc"] = carrier
        payload = {"guid": "", "data": [item]}
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                resp = await client.post(self._url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"17track web request failed: {exc}") from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("17track web returned a non-JSON response") from exc

        # New gateway shape: {"id":0,"shipments":[...],"meta":{"code":...}}
        meta = body.get("meta") or {}
        if meta.get("code") not in (None, 0):
            # protected / rejected — fall back quietly
            raise ProviderError(f"17track web rejected request (meta {meta.get('code')})")

        shipment = self._first_shipment(body)
        if not shipment:
            return None
        return self._normalize_web(number, carrier, shipment, body)

    @staticmethod
    def _first_shipment(body: dict) -> Optional[dict]:
        for key in ("shipments", "data", "dat"):
            val = body.get(key)
            if isinstance(val, list) and val:
                return val[0]
        return None

    def _normalize_web(self, number, carrier, shipment: dict, body: dict) -> TrackResult:
        """Best-effort mapping of the web shape into TrackResult.

        The web payload is undocumented and may change; we defensively read the
        fields we know and leave the rest in `raw`.
        """
        # The web payload is undocumented; defend against unexpected shapes.
        track = shipment.get("track_info") or shipment.get("track") or {}
        if not isinstance(track, dict):
            track = {}
        latest = track.get("latest_status")
        if not isinstance(latest, dict):
            latest = {}
        # Clamp upstream status to the known vocabulary (it may be a code/phrase).
        status = coerce_status(latest.get("status") or shipment.get("status"))

        raw_events = (
            track.get("z0")
            or track.get("events")
            or (track.get("tracking") or {}).get("events")
            or []
        )
        events = [_event(e) for e in raw_events if isinstance(e, dict)]
        events.sort(key=lambda e: e.time_utc or e.time_iso or "", reverse=True)

        carrier_code = carrier or shipment.get("fc") or shipment.get("carrier")
        return TrackResult(
            number=number,
            carrier=_carrier(carrier_code),
            status=status,
            status_label=status_label(status),
            sub_status=latest.get("sub_status"),
            is_delivered=status == "Delivered",
            latest_event=events[0] if events else None,
            origin=_addr((shipment.get("shipping_info") or {}).get("shipper_address")),
            destination=_addr((shipment.get("shipping_info") or {}).get("recipient_address")),
            milestones=build_milestones(events, status),
            events=events,
            providers=[c for c in [_carrier(carrier_code)] if c],
            source="web",
            fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            raw=shipment if self.settings.include_raw else None,
        )
