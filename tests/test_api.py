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


# ── scrape provider: parse a REAL captured t.17track.net/track/restapi payload ──
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from app.providers import build_chain  # noqa: E402
from app.providers.scrape import ScrapeProvider, parse_restapi_payload  # noqa: E402
from app.config import Settings  # noqa: E402

_FIXTURE = Path(__file__).parent / "fixtures" / "restapi_dhl.json"


def test_scrape_payload_parses_real_data():
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = parse_restapi_payload(data, "00340434498968565356", 7041, include_raw=True)
    assert result is not None
    assert result.source == "scrape"
    assert result.carrier.code == 7041
    assert result.carrier.name == "DHL Paket"
    assert result.status == "InfoReceived"
    assert result.status_label == "Info Received"
    assert len(result.events) >= 1
    assert result.latest_event and "DHL" in (result.latest_event.description or "")
    assert len(result.milestones) == 4
    assert result.milestones[0].reached is True  # InfoReceived reached


def test_scrape_payload_not_trackable_returns_none():
    # a shipment with a non-success code → no confident data
    assert parse_restapi_payload({"shipments": [{"code": 404}]}, "X12345", None, False) is None


def test_scrape_mode_is_scrape_only_by_default():
    chain = build_chain(Settings(provider_mode="scrape", scrape_fallback_mock=False))
    assert [p.name for p in chain] == ["scrape"]   # no demo fallback
    assert isinstance(chain[0], ScrapeProvider)


def test_scrape_fallback_mock_adds_mock():
    chain = build_chain(Settings(provider_mode="scrape", scrape_fallback_mock=True))
    assert [p.name for p in chain] == ["scrape", "mock"]


def test_get_solver_factory():
    from app.providers.solver import TwoCaptchaSolver, get_solver
    assert get_solver(Settings(solver_provider="", solver_api_key="")) is None
    assert get_solver(Settings(solver_provider="twocaptcha", solver_api_key="")) is None
    s = get_solver(Settings(solver_provider="twocaptcha", solver_api_key="abc"))
    assert isinstance(s, TwoCaptchaSolver)
