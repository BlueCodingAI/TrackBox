# 📦 TrackBox — Tracking API + UI

A 17track-style package tracking service: enter a tracking number, get the full
journey back — current status, milestone progress, estimated delivery, origin →
destination, and every scan event. FastAPI backend + a clean, modern, build-free
web UI.

![status](https://img.shields.io/badge/status-ready-22c98a) ![python](https://img.shields.io/badge/python-3.11%2B-5b8cff)

---

## Quick start

```powershell
# from d:\Project\tracking_api
./run.ps1
```

Then open **http://localhost:8000**. By default it runs in **`scrape` mode** —
real tracking data pulled via a headless browser, **no API key**, with automatic
fallback to realistic demo data if a lookup fails. (Set `PROVIDER_MODE=mock` in
`.env` for pure offline demo data.)

<details>
<summary>Manual setup (any OS)</summary>

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
</details>

---

## How data is sourced

The backend uses a **provider chain** so it works today and upgrades cleanly:

| Mode (`PROVIDER_MODE`) | Behaviour |
|---|---|
| `scrape` *(default in `.env`)* | **Real data, no API key.** Drives a headless browser to clear 17track's Cloudflare and capture the live tracking JSON. No demo fallback by default; optional captcha solver when blocked. See below. |
| `auto` | Official API if a key is set, else the public HTTP endpoint — **always** falls back to demo data. |
| `mock` | Realistic, deterministic demo data. No network. |
| `official` | Official [17track API](https://api.17track.net) only. Requires `SEVENTEENTRACK_API_KEY`. |
| `web` | Public `t.17track.net` HTTP endpoint only (no key, usually blocked by anti-bot). |

### `scrape` mode — real data without a key (how it works & caveats)

17track is protected two ways: the tracking endpoint needs a **signed token**
their JavaScript generates, and the site sits behind **Cloudflare** bot
management. So a plain HTTP call can't work (that's why `web` mode fails, and why
the `py17track` library disabled its anonymous tracker).

`scrape` mode gets around this by letting a **real browser do the work**:

1. [Playwright](https://playwright.dev) drives a headless browser to
   `www.17track.net` — the browser runs 17track's JS and clears Cloudflare.
2. It enters the number and clicks **Track**, and we intercept the response from
   `POST https://t.17track.net/track/restapi` — the same structured payload the
   site itself renders.
3. That payload has the same shape as the official API, so it's parsed into the
   normal `TrackResult` and shown in the UI like any other source.

A dedicated worker thread keeps one browser context alive, so Cloudflare clearance
is reused across lookups (first lookup ~5–10s, later ones faster).

**Captcha solver (optional).** If the browser can't clear Cloudflare on its own,
a solver can be used: the Turnstile challenge is captured, sent to the solver,
and the returned token is injected. Configure in `.env`:
```ini
SOLVER_PROVIDER=twocaptcha
SOLVER_API_KEY=your_2captcha_key
```
The solved token is generated on the solver's IP, while Cloudflare often binds
clearance to *your* IP — so it helps but isn't guaranteed from a datacenter/VPS.
If it still fails, add a residential proxy (`SCRAPE_PROXY=http://user:pass@host:port`)
so the browser clears Cloudflare directly.

By default scrape mode has **no demo fallback** — it returns real data or an
honest "not found". Set `SCRAPE_FALLBACK_MOCK=true` to fall back to demo data.

> ⚠️ **Honest caveats.** This is inherently fragile and best-effort: it depends on
> 17track's page structure and Cloudflare behaviour, it's slower than an API, it
> may be rate-limited or blocked on repeated requests (especially from a VPS IP),
> and scraping is against 17track's ToS. For production-grade reliability, use the
> free official API below.

**Browser requirement:** uses your system **Edge** by default (no download). If
Edge isn't present, install Playwright's Chromium once:
```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```
(`run.ps1` does this automatically when needed.)

### Alternative: official API (most reliable, free 100 lookups)

1. Create an account at **https://api.17track.net** and copy your Access Key
   (Settings → Security → Access Key).
2. In `.env` set:
   ```ini
   PROVIDER_MODE=auto
   SEVENTEENTRACK_API_KEY=your_key_here
   ```
3. Restart. Real tracking data flows through the same UI — no code changes.

---

## API

The endpoints are documented below. **Interactive Swagger docs are disabled by
default** (so end users can't see the API surface). To enable them while
developing, set `ENABLE_DOCS=true` in `.env` and restart — then visit **`/docs`**
(ReDoc at `/redoc`, schema at `/openapi.json`).

### `GET /api/track`
| Query | Required | Notes |
|---|---|---|
| `number` | yes | 5–50 letters, digits or hyphens |
| `carrier` | no | 17track carrier code (e.g. `7041` = DHL Paket) |

```bash
curl "http://localhost:8000/api/track?number=00340434498968565356&carrier=7041"
```

```jsonc
{
  "ok": true,
  "result": {
    "number": "00340434498968565356",
    "carrier": { "code": 7041, "name": "DHL Paket", "country": "DE", "url": "..." },
    "status": "InTransit",
    "status_label": "In Transit",
    "is_delivered": false,
    "latest_event": { "time_utc": "...", "description": "...", "location": "..." },
    "estimated_delivery": { "source": "Estimated", "from": "...", "to": "..." },
    "origin": { "country": "DE", "city": "Frankfurt" },
    "destination": { "country": "US", "city": "New York" },
    "metrics": { "days_after_order": 4, "days_of_transit": 3, "days_after_last_update": 0 },
    "milestones": [ { "stage": "InfoReceived", "reached": true, "time_utc": "..." }, ... ],
    "events": [ { "time_utc": "...", "description": "...", "location": "...", "stage": "..." }, ... ],
    "source": "mock"
  }
}
```

### `GET /api/carriers?q=dhl&limit=8`
Carrier name search for the UI picker. Backed by 17track's full carrier list
(3,393 carriers) in [`app/data/carriers.json`](app/data/carriers.json).

### `GET /api/health`
Reports `mode` and whether an official key is configured.

---

## Project layout

```
tracking_api/
├── app/
│   ├── main.py            FastAPI app: routes + static frontend
│   ├── config.py          env-driven settings
│   ├── models.py          normalized TrackResult contract + status vocabulary
│   ├── normalize.py       upstream → TrackResult mapping + milestone logic
│   ├── carriers.py        carrier code ↔ name lookup
│   ├── data/carriers.json generated carrier list
│   └── providers/
│       ├── base.py        provider interface
│       ├── mock.py        deterministic realistic demo data
│       ├── official.py    official 17track API client
│       └── web.py         best-effort public endpoint client
├── frontend/              index.html · styles.css · app.js  (no build step)
├── tests/test_api.py
├── requirements.txt · .env.example · run.ps1
```

The frontend also understands 17track-style deep links, e.g.
`http://localhost:8000/#nums=00340434498968565356&fc=7041` auto-tracks on load.

---

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

---

## Notes & limitations

- Demo data is **synthetic** but deterministic per tracking number — the same
  number always yields the same plausible journey.
- For production live tracking, the official API path is recommended (reliable,
  ToS-compliant). Scraping the public site is fragile by nature.
