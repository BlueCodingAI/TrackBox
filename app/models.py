"""Normalized tracking data contract returned by the API.

These models are provider-agnostic: the mock, official and web providers all
produce a `TrackResult`, so the frontend has a single, stable shape to render.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# ── Status vocabulary (mirrors 17track's main statuses) ──────────────────────
STATUS_LABELS: dict[str, str] = {
    "NotFound": "Not Found",
    "InfoReceived": "Info Received",
    "InTransit": "In Transit",
    "Expired": "Expired",
    "AvailableForPickup": "Available for Pickup",
    "OutForDelivery": "Out for Delivery",
    "DeliveryFailure": "Delivery Failure",
    "Delivered": "Delivered",
    "Exception": "Exception",
}

# Canonical milestone progression shown as a progress bar in the UI.
MILESTONE_ORDER: list[str] = ["InfoReceived", "InTransit", "OutForDelivery", "Delivered"]

# Map any granular stage/status onto one of the milestone buckets above.
_STAGE_TO_MILESTONE: dict[str, str] = {
    "InfoReceived": "InfoReceived",
    "PickedUp": "InTransit",
    "Departure": "InTransit",
    "Arrival": "InTransit",
    "InTransit": "InTransit",
    "CustomsProcessing": "InTransit",
    "CustomsReleased": "InTransit",
    "AvailableForPickup": "OutForDelivery",
    "OutForDelivery": "OutForDelivery",
    "Delivered": "Delivered",
}


def status_label(status: Optional[str]) -> str:
    if not status:
        return "Unknown"
    return STATUS_LABELS.get(status, status)


def coerce_status(status: Optional[object], default: str = "InTransit") -> str:
    """Clamp an upstream status to the known vocabulary.

    Guards against unstable/undocumented sources (e.g. the web gateway) sending
    numeric codes or localized phrases that would break is_delivered / milestone
    logic and the frontend's status styling.
    """
    return status if status in STATUS_LABELS else default


def milestone_for_stage(stage: Optional[str]) -> Optional[str]:
    if not stage:
        return None
    return _STAGE_TO_MILESTONE.get(stage)


class Carrier(BaseModel):
    code: Optional[int] = None
    name: Optional[str] = None
    country: Optional[str] = None
    url: Optional[str] = None


class Address(BaseModel):
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None

    def is_empty(self) -> bool:
        return not any([self.country, self.state, self.city, self.postal_code])


class Event(BaseModel):
    time_iso: Optional[str] = None  # local time of the scan, ISO-8601
    time_utc: Optional[str] = None  # same instant in UTC
    description: Optional[str] = None
    location: Optional[str] = None
    stage: Optional[str] = None
    sub_status: Optional[str] = None
    address: Optional[Address] = None


class Milestone(BaseModel):
    stage: str
    label: str
    time_utc: Optional[str] = None
    reached: bool = False


class EstimatedDelivery(BaseModel):
    source: Optional[str] = None
    from_time: Optional[str] = Field(default=None, alias="from")
    to_time: Optional[str] = Field(default=None, alias="to")

    model_config = {"populate_by_name": True}


class Metrics(BaseModel):
    days_after_order: Optional[int] = None
    days_of_transit: Optional[int] = None
    days_after_last_update: Optional[int] = None


class TrackResult(BaseModel):
    number: str
    carrier: Optional[Carrier] = None
    status: str = "NotFound"
    status_label: str = "Not Found"
    sub_status: Optional[str] = None
    is_delivered: bool = False
    latest_event: Optional[Event] = None
    estimated_delivery: Optional[EstimatedDelivery] = None
    origin: Optional[Address] = None
    destination: Optional[Address] = None
    metrics: Optional[Metrics] = None
    milestones: list[Milestone] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)  # newest first
    providers: list[Carrier] = Field(default_factory=list)
    fetched_at: Optional[str] = None
    # Internal-only fields — kept as attributes for logic/debugging but NEVER
    # serialized to clients (exclude=True), so the data source/method stays hidden.
    source: str = Field(default="mock", exclude=True)
    note: Optional[str] = Field(default=None, exclude=True)
    raw: Optional[dict] = Field(default=None, exclude=True)


class TrackResponse(BaseModel):
    ok: bool = True
    result: Optional[TrackResult] = None
    error: Optional[str] = None
