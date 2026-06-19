"""Headless-browser scrape provider (no API key).

Drives 17track's own website with Playwright so the browser executes their JS,
clears the Cloudflare challenge, and makes the *signed* request to
`https://t.17track.net/track/restapi`. We intercept that response — it carries
the same structured payload as the official API (`shipment` == `track_info`),
so we reuse `normalize_official` to parse it.

Design notes
------------
* Playwright's sync API can't run inside the asyncio event loop, and on Windows
  its teardown clashes with uvicorn's Proactor loop. So all browser work runs in
  ONE dedicated daemon thread that owns the Playwright instance + a persistent
  browser context. The context keeps its Cloudflare-clearance cookies, so the
  first lookup pays the challenge cost (~3-8s) and later ones are faster.
* Jobs are serialized through a queue (a browser context is not concurrency
  safe). The async `track()` submits a job and awaits its result without
  blocking the event loop.

This path is inherently fragile (it depends on 17track's page structure and
anti-bot behaviour) and is best-effort: any failure raises `ProviderError` so
the resolver can fall back to demo data.
"""
from __future__ import annotations

import asyncio
import atexit
import queue
import threading
from concurrent.futures import Future
from typing import Optional

from ..config import Settings
from ..models import TrackResult
from ..normalize import normalize_official
from .base import ProviderError, TrackingProvider

def parse_restapi_payload(
    data: dict,
    number: str,
    carrier: Optional[int],
    include_raw: bool,
) -> Optional[TrackResult]:
    """Parse a `t.17track.net/track/restapi` JSON body into a TrackResult.

    Pure function (no browser) so it can be unit-tested against a real payload.
    Returns None when the number isn't trackable / has no data yet.
    """
    shipments = data.get("shipments") or []
    if not shipments:
        meta = data.get("meta") or {}
        raise ProviderError(f"17track returned no shipments (meta {meta.get('code')})")

    ship = shipments[0]
    if ship.get("code") not in (200, 0, None):
        return None  # e.g. 400/404 → not trackable

    track_info = ship.get("shipment")
    if not track_info:
        return None

    item = {
        "number": ship.get("number") or number,
        "carrier": ship.get("carrier") or carrier,
        "track_info": track_info,
    }
    result = normalize_official(number, carrier, item, include_raw, source="scrape")
    if result.status == "NotFound" and not result.events:
        return None
    return result


_HOMEPAGE = "https://www.17track.net/en"
_RESTAPI_MARK = "track/restapi"
_TEXTAREA = "textarea#auto-size-textarea"
_TRACK_BTN = "div[class*='batch_track_search']"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)


class _BrowserWorker:
    """Owns Playwright + a persistent browser context in a single thread."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._q: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._fatal: Optional[str] = None
        atexit.register(self.shutdown)

    # ── public API (called from the event-loop thread) ──────────────────────
    def submit(self, number: str, carrier: Optional[int]) -> "Future":
        self._ensure_started()
        if self._fatal:
            fut: Future = Future()
            fut.set_exception(ProviderError(self._fatal))
            return fut
        fut = Future()
        self._q.put((number, carrier, fut))
        return fut

    def shutdown(self) -> None:
        if self._thread and self._thread.is_alive():
            self._q.put(None)

    # ── worker thread internals ─────────────────────────────────────────────
    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="scrape-browser", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._fatal = "Playwright not installed (pip install playwright)"
            self._drain_with_error()
            return

        try:
            with sync_playwright() as p:
                browser = self._launch(p)
                ctx = browser.new_context(
                    user_agent=_UA, locale="en-US",
                    viewport={"width": 1366, "height": 900},
                )
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                )
                self._loop(ctx)
                ctx.close()
                browser.close()
        except Exception as exc:  # launch failed (e.g. no browser installed)
            self._fatal = f"browser launch failed: {exc}"
            self._drain_with_error()

    def _launch(self, p):
        timeout_ms = int(self._settings.scrape_timeout * 1000)
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run"]
        last: Exception | None = None
        # Prefer the system Edge (no download); fall back to bundled Chromium.
        for channel in (self._settings.scrape_browser_channel, None):
            try:
                kwargs = {"headless": self._settings.scrape_headless, "args": args,
                          "timeout": timeout_ms}
                if channel:
                    kwargs["channel"] = channel
                return p.chromium.launch(**kwargs)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise RuntimeError(
            f"{last}. Install a browser: `python -m playwright install chromium`."
        )

    def _loop(self, ctx) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            number, carrier, fut = job
            if fut.set_running_or_notify_cancel() is False:
                continue
            try:
                fut.set_result(self._scrape(ctx, number, carrier))
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)

    def _scrape(self, ctx, number: str, carrier: Optional[int]) -> dict:
        timeout_ms = int(self._settings.scrape_timeout * 1000)
        page = ctx.new_page()
        try:
            page.goto(_HOMEPAGE, wait_until="load", timeout=timeout_ms)
            page.wait_for_selector(_TEXTAREA, timeout=timeout_ms)

            # dismiss the promo banner so it can't intercept the TRACK click
            for sel in ("[class*=BannerReport_closeBtn]", "[class*=closeBtn]"):
                try:
                    page.click(sel, timeout=1200)
                    break
                except Exception:
                    pass

            page.fill(_TEXTAREA, number)
            with page.expect_response(
                lambda r: _RESTAPI_MARK in r.url and r.request.method == "POST",
                timeout=timeout_ms,
            ) as resp_info:
                page.locator(_TRACK_BTN).first.click(timeout=timeout_ms)
            resp = resp_info.value
            if resp.status != 200:
                raise ProviderError(f"17track restapi returned HTTP {resp.status}")
            return resp.json()
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _drain_with_error(self) -> None:
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                return
            if job is None:
                return
            _, _, fut = job
            if not fut.done():
                fut.set_exception(ProviderError(self._fatal or "scrape unavailable"))


class ScrapeProvider(TrackingProvider):
    name = "scrape"

    _worker: Optional[_BrowserWorker] = None  # process-wide singleton

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if ScrapeProvider._worker is None:
            ScrapeProvider._worker = _BrowserWorker(settings)

    async def track(self, number: str, carrier: Optional[int] = None) -> Optional[TrackResult]:
        worker = ScrapeProvider._worker
        assert worker is not None
        fut = worker.submit(number, carrier)
        try:
            data = await asyncio.wait_for(
                asyncio.wrap_future(fut), timeout=self.settings.scrape_timeout + 15
            )
        except asyncio.TimeoutError as exc:
            raise ProviderError("scrape timed out") from exc
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"scrape failed: {exc}") from exc

        return parse_restapi_payload(data, number, carrier, self.settings.include_raw)
