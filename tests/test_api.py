"""API tests. Forces demo mode so they run offline and deterministically."""
import os

os.environ["PROVIDER_MODE"] = "mock"
os.environ["INCLUDE_RAW"] = "true"
os.environ["ENABLE_DOCS"] = "false"  # pin so tests don't depend on local .env

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # health must not reveal the provider mode / data source
    assert "mode" not in body
    assert "official_key" not in body


def test_docs_disabled():
    # API surface (Swagger/OpenAPI) is hidden from users
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_track_returns_full_result():
    r = client.get("/api/track", params={"number": "00340434498968565356", "carrier": 7041})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    res = body["result"]
    assert res["number"] == "00340434498968565356"
    assert res["carrier"]["name"] == "DHL Paket"
    # internal fields must NOT leak to clients
    assert "source" not in res
    assert "note" not in res
    assert "raw" not in res
    assert res["status"] in {
        "Delivered", "InTransit", "OutForDelivery", "InfoReceived",
        "AvailableForPickup", "DeliveryFailure", "Exception",
    }
    assert res["status_label"]
    assert len(res["events"]) >= 1
    assert len(res["milestones"]) == 4
    # newest-first ordering
    times = [e["time_utc"] for e in res["events"] if e.get("time_utc")]
    assert times == sorted(times, reverse=True)


def test_track_is_deterministic():
    p = {"number": "LX123456789CN"}
    a = client.get("/api/track", params=p).json()["result"]
    b = client.get("/api/track", params=p).json()["result"]
    assert a["status"] == b["status"]
    assert len(a["events"]) == len(b["events"])
    assert a["carrier"]["code"] == b["carrier"]["code"]


def test_milestones_consistent_with_status():
    res = client.get("/api/track", params={"number": "DELIVEREDTEST123"}).json()["result"]
    if res["is_delivered"]:
        assert all(m["reached"] for m in res["milestones"])


def test_invalid_number_rejected():
    r = client.get("/api/track", params={"number": "@@@"})
    body = r.json()
    assert body["ok"] is False
    assert "Invalid" in body["error"]


def test_carrier_search():
    r = client.get("/api/carriers", params={"q": "dhl", "limit": 5})
    assert r.status_code == 200
    names = [c["name"].lower() for c in r.json()["carriers"]]
    assert any("dhl" in n for n in names)


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "TrackBox" in r.text


# ── scrape provider: parse REAL captured parcelsapp.com/api/v2/parcels payloads ──
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.providers import build_chain, ProviderError  # noqa: E402
from app.providers.scrape import ScrapeProvider, parse_parcels_payload  # noqa: E402
from app.config import Settings  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures"


def test_scrape_payload_parses_real_dhl_data():
    data = json.loads((_FIXTURES / "parcelsapp_dhl.json").read_text(encoding="utf-8"))
    result = parse_parcels_payload(data, "00340434657256528415", include_raw=True)
    assert result is not None
    assert result.source == "scrape"
    assert result.carrier.name == "Deutsche Post - DHL Paket"
    # archive + sub_status "delivered" → Delivered
    assert result.status == "Delivered"
    assert result.status_label == "Delivered"
    assert result.is_delivered is True
    assert len(result.events) == 6
    # newest first, latest is the delivery scan (German text passed through verbatim)
    assert result.latest_event and "zugestellt" in (result.latest_event.description or "")
    times = [e.time_utc for e in result.events if e.time_utc]
    assert times == sorted(times, reverse=True)
    assert len(result.milestones) == 4
    assert all(m.reached for m in result.milestones)  # delivered → full bar


def test_scrape_payload_parses_ups_with_locations_and_metrics():
    data = json.loads((_FIXTURES / "parcelsapp_ups.json").read_text(encoding="utf-8"))
    result = parse_parcels_payload(data, "1Z999AA10123456784", include_raw=False)
    assert result is not None
    assert result.carrier.name == "UPS"
    assert result.status == "Delivered"
    # destination from the "to" field; origin from the oldest located scan
    assert result.destination and result.destination.city == "Longview"
    assert result.destination.state == "TX"
    assert result.origin and result.origin.city == "Los Angeles"
    # "days_transit" attribute → metrics
    assert result.metrics and result.metrics.days_of_transit == 175
    assert result.latest_event.location == "Longview, TX, US"


def test_scrape_payload_no_data_returns_none():
    assert parse_parcels_payload({"error": "NO_DATA"}, "X12345", False) is None
    assert parse_parcels_payload({"error": "NO_TRACKER", "services": []}, "X12345", False) is None
    assert parse_parcels_payload({}, "X12345", False) is None


def test_scrape_payload_archive_without_delivery_is_not_delivered():
    # parcelsapp marks stalled shipments "archive" with no delivery sub_status —
    # must NOT be reported as Delivered, and must NOT fake an "In Transit"
    # milestone for a parcel that never left the seller.
    data = {
        "states": [{"date": "2025-06-14T05:16:19Z", "carrier": 0,
                    "status": "Pending shipping by the seller"}],
        "carriers": ["China Post"],
        "status": "archive",
    }
    result = parse_parcels_payload(data, "LX123456789CN", False)
    assert result is not None
    assert result.is_delivered is False
    assert result.status == "Expired"
    ms = {m.stage: m.reached for m in result.milestones}
    assert ms["InfoReceived"] is True
    assert ms["InTransit"] is False  # never shipped — no phantom transit milestone


def test_scrape_payload_in_transit_partial_milestones():
    # A genuinely in-transit parcel: status reflects InTransit, not delivered,
    # and the milestone bar is partially filled (InTransit reached, Delivered not).
    data = {
        "states": [
            {"location": "Frankfurt, DE", "date": "2026-06-10T09:00:00Z", "carrier": 0,
             "status": "Departed from facility"},
            {"location": "Shenzhen, CN", "date": "2026-06-08T02:00:00Z", "carrier": 0,
             "status": "Shipping label created"},
        ],
        "carriers": ["DHL"],
        "status": "transit",
    }
    result = parse_parcels_payload(data, "TRANSIT123", False)
    assert result is not None
    assert result.status == "InTransit"
    assert result.is_delivered is False
    ms = {m.stage: m.reached for m in result.milestones}
    assert ms["InTransit"] is True
    assert ms["Delivered"] is False
    assert result.origin and result.origin.city == "Shenzhen"  # oldest located scan


def test_scrape_payload_negated_delivery_is_not_delivered():
    # "could not be delivered" must NOT be read as Delivered (no confetti!).
    data = {
        "states": [{"date": "2026-06-18T18:00:00Z", "carrier": 0,
                    "status": "The package could not be delivered to the recipient"}],
        "carriers": ["UPS"],
        "status": "archive",
    }
    result = parse_parcels_payload(data, "FAIL123", False)
    assert result is not None
    assert result.is_delivered is False
    assert result.status in ("DeliveryFailure", "Exception")


def test_scrape_payload_transient_error_raises():
    # A non-terminal error with no timeline is transient → ProviderError (so the
    # resolver logs it), NOT a silent "not found".
    with pytest.raises(ProviderError):
        parse_parcels_payload({"error": "TEMP_FAIL"}, "X12345", False)


def test_scrape_mode_is_scrape_only_by_default():
    chain = build_chain(Settings(provider_mode="scrape", scrape_fallback_mock=False))
    assert [p.name for p in chain] == ["scrape"]   # no demo fallback
    assert isinstance(chain[0], ScrapeProvider)


def test_scrape_fallback_mock_adds_mock():
    chain = build_chain(Settings(provider_mode="scrape", scrape_fallback_mock=True))
    assert [p.name for p in chain] == ["scrape", "mock"]


def test_parse_proxy():
    from app.providers.scrape import parse_proxy
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None
    p = parse_proxy("http://user:p%40ss@gw.example.com:8080")
    assert p["scheme"] == "http" and p["host"] == "gw.example.com" and p["port"] == 8080
    assert p["username"] == "user" and p["password"] == "p@ss"   # %40 → @ (decoded)
    p2 = parse_proxy("gw.example.com:1234")   # scheme optional
    assert p2["scheme"] == "http" and p2["host"] == "gw.example.com" and p2["port"] == 1234


def test_get_solver_factory():
    from app.providers.solver import TwoCaptchaSolver, get_solver
    assert get_solver(Settings(solver_provider="", solver_api_key="")) is None
    assert get_solver(Settings(solver_provider="twocaptcha", solver_api_key="")) is None
    s = get_solver(Settings(solver_provider="twocaptcha", solver_api_key="abc"))
    assert isinstance(s, TwoCaptchaSolver)
