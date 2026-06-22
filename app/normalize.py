"""Mapping from upstream 17track payloads into the normalized `TrackResult`.

Also hosts `build_milestones`, shared by the mock provider and the real ones.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import carriers
from .models import (
    MILESTONE_ORDER,
    Address,
    Carrier,
    EstimatedDelivery,
    Event,
    Metrics,
    Milestone,
    TrackResult,
    milestone_for_stage,
    status_label,
)

# Map a top-level status onto the furthest milestone it implies.
# "Expired" is deliberately mapped to "InfoReceived" (not "InTransit"): an
# expired/archived parcel may never have actually shipped, so we must NOT
# fabricate an "In Transit" milestone — let the event stages raise the bar if
# (and only if) a real scan supports it.
_STATUS_TO_MILESTONE = {
    "InfoReceived": "InfoReceived",
    "InTransit": "InTransit",
    "Expired": "InfoReceived",
    "Exception": "InTransit",
    "AvailableForPickup": "OutForDelivery",
    "OutForDelivery": "OutForDelivery",
    "DeliveryFailure": "OutForDelivery",
    "Delivered": "Delivered",
}


def build_milestones(events: list[Event], status: Optional[str]) -> list[Milestone]:
    """Compute the 4-step milestone bar from event stages + the current status."""
    from .models import STATUS_LABELS  # local import avoids cycle at module load

    reached_idx = -1
    first_time: dict[int, str] = {}

    # Events are expected newest-first; walk oldest-first for "first reached" time.
    for ev in reversed(events):
        ms = milestone_for_stage(ev.stage)
        if ms is None:
            continue
        idx = MILESTONE_ORDER.index(ms)
        reached_idx = max(reached_idx, idx)
        t = ev.time_utc or ev.time_iso
        if idx not in first_time and t:
            first_time[idx] = t

    status_ms = _STATUS_TO_MILESTONE.get(status or "")
    if status_ms is not None:
        reached_idx = max(reached_idx, MILESTONE_ORDER.index(status_ms))

    out: list[Milestone] = []
    for i, stage in enumerate(MILESTONE_ORDER):
        out.append(
            Milestone(
                stage=stage,
                label=STATUS_LABELS.get(stage, stage),
                time_utc=first_time.get(i),
                reached=i <= reached_idx,
            )
        )
    return out


def _addr(d: Optional[dict]) -> Optional[Address]:
    if not d:
        return None
    a = Address(
        country=d.get("country"),
        state=d.get("state"),
        city=d.get("city"),
        postal_code=d.get("postal_code"),
    )
    return None if a.is_empty() else a


def _carrier(code: Optional[int], name: Optional[str] = None) -> Optional[Carrier]:
    info = carriers.carrier_info(code)
    if info:
        return Carrier(**info)
    if code is None and not name:
        return None
    return Carrier(code=code, name=name)


def _event(d: dict) -> Event:
    return Event(
        time_iso=d.get("time_iso"),
        time_utc=d.get("time_utc"),
        description=d.get("description"),
        location=d.get("location"),
        stage=d.get("stage"),
        sub_status=d.get("sub_status"),
        address=_addr(d.get("address")),
    )


def normalize_official(
    number: str,
    carrier_param: Optional[int],
    item: dict,
    include_raw: bool,
    source: str = "official",
) -> TrackResult:
    """Map one tracking entry into a TrackResult.

    Used by both the official API (`accepted[].track_info`) and the web-scrape
    provider (`shipments[].shipment`), since the two payloads share the same
    inner shape — the caller just wraps it as `{number, carrier, track_info}`.
    """
    info = item.get("track_info") or {}
    latest_status = info.get("latest_status") or {}
    status = latest_status.get("status") or "NotFound"
    sub_status = latest_status.get("sub_status")

    # Flatten every provider's events into one history (newest first).
    raw_events: list[dict] = []
    tracking = info.get("tracking") or {}
    for prov in tracking.get("providers") or []:
        raw_events.extend(prov.get("events") or [])

    latest_event_raw = info.get("latest_event")
    if isinstance(latest_event_raw, dict) and latest_event_raw not in raw_events:
        raw_events.append(latest_event_raw)

    events = [_event(e) for e in raw_events if isinstance(e, dict)]
    events.sort(key=lambda e: e.time_utc or e.time_iso or "", reverse=True)

    # Carriers that handled the parcel.
    providers: list[Carrier] = []
    for prov in tracking.get("providers") or []:
        p = prov.get("provider") or {}
        c = _carrier(p.get("key"), p.get("name"))
        if c:
            providers.append(c)

    carrier_code = item.get("carrier") or carrier_param
    main_carrier = _carrier(carrier_code) or (providers[0] if providers else None)

    shipping = info.get("shipping_info") or {}
    metrics_raw = info.get("time_metrics") or {}
    edd = (metrics_raw.get("estimated_delivery_date") or {}) if metrics_raw else {}

    result = TrackResult(
        number=number,
        carrier=main_carrier,
        status=status,
        status_label=status_label(status),
        sub_status=sub_status,
        is_delivered=status == "Delivered",
        latest_event=_event(latest_event_raw) if isinstance(latest_event_raw, dict) else (events[0] if events else None),
        estimated_delivery=(
            EstimatedDelivery(
                source=edd.get("source"),
                **{"from": edd.get("from"), "to": edd.get("to")},
            )
            if edd
            else None
        ),
        origin=_addr(shipping.get("shipper_address")),
        destination=_addr(shipping.get("recipient_address")),
        metrics=Metrics(
            days_after_order=metrics_raw.get("days_after_order"),
            days_of_transit=metrics_raw.get("days_of_transit"),
            days_after_last_update=metrics_raw.get("days_after_last_update"),
        )
        if metrics_raw
        else None,
        milestones=build_milestones(events, status),
        events=events,
        providers=providers,
        source=source,
        fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        raw=item if include_raw else None,
    )
    return result
