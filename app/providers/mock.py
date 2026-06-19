"""Deterministic, realistic demo data.

Given the same tracking number (+ carrier) you always get the same plausible
journey, so the UI is stable and demoable without any network or API key.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import carriers
from ..models import (
    Address,
    Carrier,
    EstimatedDelivery,
    Event,
    Metrics,
    TrackResult,
    status_label,
)
from ..normalize import build_milestones
from .base import TrackingProvider

# Small curated geography. tz = UTC offset in hours.
_PLACES: dict[str, dict] = {
    "CN": {"tz": 8, "cities": ["Shenzhen", "Guangzhou", "Shanghai", "Yiwu", "Hangzhou"]},
    "US": {"tz": -5, "cities": ["New York, NY", "Los Angeles, CA", "Chicago, IL", "Dallas, TX", "Atlanta, GA"]},
    "DE": {"tz": 1, "cities": ["Frankfurt", "Berlin", "Hamburg", "Cologne", "Munich"]},
    "GB": {"tz": 0, "cities": ["London", "Manchester", "Birmingham", "Leeds"]},
    "FR": {"tz": 1, "cities": ["Paris", "Lyon", "Marseille"]},
    "JP": {"tz": 9, "cities": ["Tokyo", "Osaka", "Nagoya"]},
    "AU": {"tz": 10, "cities": ["Sydney", "Melbourne", "Brisbane"]},
    "CA": {"tz": -5, "cities": ["Toronto, ON", "Vancouver, BC", "Montreal, QC"]},
    "NL": {"tz": 1, "cities": ["Amsterdam", "Rotterdam", "Utrecht"]},
    "ES": {"tz": 1, "cities": ["Madrid", "Barcelona", "Valencia"]},
}
_COUNTRY_NAMES = {
    "CN": "China", "US": "United States", "DE": "Germany", "GB": "United Kingdom",
    "FR": "France", "JP": "Japan", "AU": "Australia", "CA": "Canada",
    "NL": "Netherlands", "ES": "Spain",
}

# Plausible carriers to "auto-detect" when none is supplied.
_DETECT_CARRIERS = [7041, 3011, 100003, 100001, 21051, 190271]  # DHL, China Post, UPS, FedEx, USPS, GLS

# Each scenario maps to (main status, sub-status, how far through the journey).
_SCENARIOS = [
    ("Delivered", "Delivered_Other", "delivered", 34),
    ("InTransit", "InTransit_Departure", "transit", 30),
    ("OutForDelivery", "OutForDelivery_Other", "out_for_delivery", 12),
    ("InfoReceived", "InfoReceived_Other", "info", 8),
    ("AvailableForPickup", "AvailableForPickup_Other", "pickup", 6),
    ("DeliveryFailure", "DeliveryFailure_NoBody", "failed", 5),
    ("Exception", "Exception_Other", "exception", 5),
]


def _seed(number: str, carrier: Optional[int]) -> random.Random:
    h = hashlib.sha256(f"{number.strip().upper()}::{carrier or ''}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fmt_local(dt_utc: datetime, tz_hours: int) -> str:
    local = dt_utc.astimezone(timezone(timedelta(hours=tz_hours)))
    return local.isoformat(timespec="seconds")


def _addr(country: str, city: str) -> Address:
    parts = city.split(", ")
    return Address(
        country=country,
        state=parts[1] if len(parts) > 1 else None,
        city=parts[0],
    )


class MockProvider(TrackingProvider):
    name = "mock"

    async def track(self, number: str, carrier: Optional[int] = None) -> Optional[TrackResult]:
        return self.build(number, carrier)

    def build(self, number: str, carrier: Optional[int] = None) -> TrackResult:
        rng = _seed(number, carrier)
        now = datetime.now(timezone.utc)

        # ── carrier + route ─────────────────────────────────────────────────
        carrier_code = carrier or rng.choice(_DETECT_CARRIERS)
        info = carriers.carrier_info(carrier_code) or {"code": carrier_code, "name": "Carrier"}
        carrier_model = Carrier(**info)

        origin_country = info.get("country") if info.get("country") in _PLACES else rng.choice(list(_PLACES))
        international = rng.random() < 0.65
        if international:
            dest_country = rng.choice([c for c in _PLACES if c != origin_country])
        else:
            dest_country = origin_country

        o_tz = _PLACES[origin_country]["tz"]
        d_tz = _PLACES[dest_country]["tz"]
        origin_city = rng.choice(_PLACES[origin_country]["cities"])
        dest_city = rng.choice(_PLACES[dest_country]["cities"])
        dest_state = dest_city.split(", ")[1] if ", " in dest_city else None

        status, sub_status, depth, _w = self._pick_scenario(rng)

        # ── build the chronological event list ──────────────────────────────
        steps = self._steps(international, depth, origin_country, origin_city,
                             dest_country, dest_city, rng)

        transit_days = rng.randint(4, 16) if international else rng.randint(1, 6)
        start = now - timedelta(days=transit_days, hours=rng.randint(0, 20))
        # last event recency depends on how "live" the parcel is
        last_gap_hours = {
            "delivered": rng.randint(2, 72),
            "transit": rng.randint(1, 30),
            "out_for_delivery": rng.randint(0, 6),
            "pickup": rng.randint(1, 36),
            "failed": rng.randint(1, 18),
            "exception": rng.randint(2, 48),
            "info": rng.randint(1, 20),
        }[depth]
        end = now - timedelta(hours=last_gap_hours)
        if end <= start:
            end = start + timedelta(hours=6)

        n = len(steps)
        events: list[Event] = []
        for i, (stage, sub, desc, country, city, tz) in enumerate(steps):
            frac = i / max(n - 1, 1)
            t_utc = start + (end - start) * frac
            events.append(
                Event(
                    time_utc=_fmt_utc(t_utc),
                    time_iso=_fmt_local(t_utc, tz),
                    description=desc,
                    location=f"{city}, {_COUNTRY_NAMES.get(country, country)}",
                    stage=stage,
                    sub_status=sub,
                    address=_addr(country, city),
                )
            )

        events.reverse()  # newest first
        latest = events[0] if events else None

        # ── estimated delivery ──────────────────────────────────────────────
        if status == "Delivered":
            edd = EstimatedDelivery(source="Official",
                                    **{"from": latest.time_utc, "to": latest.time_utc}) if latest else None
        elif depth in ("transit", "out_for_delivery", "pickup", "info"):
            base = now + timedelta(days=1 if depth in ("out_for_delivery", "pickup") else rng.randint(1, 5))
            edd = EstimatedDelivery(
                source="Estimated",
                **{"from": _fmt_utc(base), "to": _fmt_utc(base + timedelta(days=2))},
            )
        else:
            edd = None

        # ── metrics ─────────────────────────────────────────────────────────
        pickup_ev = next((e for e in reversed(events) if e.stage == "PickedUp"), None)
        last_utc = _parse(latest.time_utc) if latest else now
        metrics = Metrics(
            days_after_order=max((now - start).days, 0),
            days_of_transit=max((last_utc - _parse(pickup_ev.time_utc)).days, 0) if pickup_ev else None,
            days_after_last_update=max((now - last_utc).days, 0),
        )

        result = TrackResult(
            number=number,
            carrier=carrier_model,
            status=status,
            status_label=status_label(status),
            sub_status=sub_status,
            is_delivered=status == "Delivered",
            latest_event=latest,
            estimated_delivery=edd,
            origin=_addr(origin_country, origin_city),
            destination=Address(country=dest_country, state=dest_state, city=dest_city.split(", ")[0]),
            metrics=metrics,
            milestones=build_milestones(events, status),
            events=events,
            providers=[carrier_model],
            source="mock",
            fetched_at=_fmt_utc(now),
            note="Demo data — configure a 17track API key or PROVIDER_MODE for live tracking.",
        )
        if self.settings.include_raw:
            result.raw = {"generated": True, "scenario": status, "depth": depth}
        return result

    # ── helpers ─────────────────────────────────────────────────────────────
    def _pick_scenario(self, rng: random.Random):
        total = sum(s[3] for s in _SCENARIOS)
        roll = rng.uniform(0, total)
        upto = 0.0
        for s in _SCENARIOS:
            upto += s[3]
            if roll <= upto:
                return s
        return _SCENARIOS[0]

    def _steps(self, international, depth, oc, ocity, dc, dcity, rng):
        """Return chronological [(stage, sub, description, country, city, tz)]."""
        o_tz, d_tz = _PLACES[oc]["tz"], _PLACES[dc]["tz"]
        full: list[tuple] = [
            ("InfoReceived", "InfoReceived_Other", "Shipping label created, carrier awaiting item", oc, ocity, o_tz),
            ("PickedUp", "InTransit_PickedUp", "Item picked up by carrier", oc, ocity, o_tz),
            ("Departure", "InTransit_Departure", f"Departed facility in {ocity}", oc, ocity, o_tz),
        ]
        if international:
            full += [
                ("Departure", "InTransit_CustomsProcessing", "Item presented to export customs", oc, ocity, o_tz),
                ("Departure", "InTransit_Departure", f"Left origin country ({_COUNTRY_NAMES.get(oc, oc)})", oc, ocity, o_tz),
                ("Arrival", "InTransit_Arrival", f"Arrived in destination country ({_COUNTRY_NAMES.get(dc, dc)})", dc, dcity, d_tz),
                ("Arrival", "InTransit_CustomsProcessing", "Import customs clearance in progress", dc, dcity, d_tz),
                ("Arrival", "InTransit_CustomsReleased", "Released by import customs", dc, dcity, d_tz),
            ]
        full += [
            ("Arrival", "InTransit_Arrival", f"Arrived at local distribution center, {dcity}", dc, dcity, d_tz),
        ]

        # truncate / finish according to scenario depth
        if depth == "info":
            return full[:1]
        if depth == "transit":
            cut = rng.randint(2, max(2, len(full) - 1))
            return full[:cut]
        if depth == "out_for_delivery":
            return full + [("OutForDelivery", "OutForDelivery_Other", f"Out for delivery in {dcity}", dc, dcity, d_tz)]
        if depth == "pickup":
            return full + [
                ("OutForDelivery", "AvailableForPickup_Other", "Arrived at pickup point", dc, dcity, d_tz),
                ("AvailableForPickup", "AvailableForPickup_Other", "Available for collection — bring ID", dc, dcity, d_tz),
            ]
        if depth == "failed":
            return full + [
                ("OutForDelivery", "OutForDelivery_Other", f"Out for delivery in {dcity}", dc, dcity, d_tz),
                ("OutForDelivery", "DeliveryFailure_NoBody", "Delivery attempted — recipient not available", dc, dcity, d_tz),
            ]
        if depth == "exception":
            cut = rng.randint(3, max(3, len(full)))
            return full[:cut] + [("Arrival", "Exception_Other", "Exception: item delayed in transit", dc, dcity, d_tz)]
        # delivered
        return full + [
            ("OutForDelivery", "OutForDelivery_Other", f"Out for delivery in {dcity}", dc, dcity, d_tz),
            ("Delivered", "Delivered_Other", f"Delivered, signed by {rng.choice(['front desk', 'recipient', 'neighbour', 'mailroom'])}", dc, dcity, d_tz),
        ]


def _parse(iso: Optional[str]) -> datetime:
    if not iso:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
