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
import logging
import queue
import threading
import time
from concurrent.futures import Future
from typing import Optional
from urllib.parse import urlparse

from ..config import Settings
from ..models import TrackResult
from ..normalize import normalize_official
from .base import ProviderError, TrackingProvider
from .solver import SolverError, get_solver

logger = logging.getLogger("tracking_api.scrape")

def _clean_num(s: Optional[str]) -> str:
    return (s or "").strip().upper().replace(" ", "")


def response_has_number(data: dict, number: str) -> bool:
    """True if the restapi payload contains a shipment for `number`."""
    target = _clean_num(number)
    return any(_clean_num(s.get("number")) == target for s in (data.get("shipments") or []))


def parse_restapi_payload(
    data: dict,
    number: str,
    carrier: Optional[int],
    include_raw: bool,
) -> Optional[TrackResult]:
    """Parse a `t.17track.net/track/restapi` JSON body into a TrackResult.

    Pure function (no browser) so it can be unit-tested against a real payload.
    Returns None when the number isn't trackable / has no data yet.

    Crucially, we pick the shipment whose number MATCHES the requested one — the
    response can contain 17track's default sample shipment or a stale result, and
    relabelling that with the user's number is exactly the "wrong data" bug. If no
    shipment matches, return None (→ honest "not found") rather than guess.
    """
    shipments = data.get("shipments") or []
    if not shipments:
        meta = data.get("meta") or {}
        raise ProviderError(f"17track returned no shipments (meta {meta.get('code')})")

    target = _clean_num(number)
    ship = next((s for s in shipments if _clean_num(s.get("number")) == target), None)
    if ship is None:
        logger.info(
            "scrape: response had no shipment for %s (got %s) — treating as not found",
            number, [s.get("number") for s in shipments],
        )
        return None

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

# Injected before page scripts. When Cloudflare assigns window.turnstile we wrap
# render() to capture the challenge params (sitekey, cData, chlPageData, action)
# and stash the callback, so a solver can produce a token and we complete the
# challenge by calling window.__tsCallback(token).
_TURNSTILE_HOOK = """
(() => {
  let _ts;
  try {
    Object.defineProperty(window, 'turnstile', {
      configurable: true,
      get() { return _ts; },
      set(v) {
        _ts = v;
        try {
          if (v && typeof v.render === 'function' && !v.__hooked) {
            v.render = (a, b) => {
              window.__cfChallenge = {
                website_key: b.sitekey, website_url: location.href,
                data: b.cData, pagedata: b.chlPageData,
                action: b.action, user_agent: navigator.userAgent
              };
              window.__tsCallback = b.callback;
              return 'cf-intercepted';
            };
            v.__hooked = true;
          }
        } catch (e) {}
      }
    });
  } catch (e) {}
})();
"""


class _BrowserWorker:
    """Owns Playwright + a persistent browser context in a single thread."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._q: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._fatal: Optional[str] = None
        self._solver = get_solver(settings)
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
                    **self._context_kwargs(),
                )
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                )
                ctx.add_init_script(_TURNSTILE_HOOK)
                self._loop(ctx)
                ctx.close()
                browser.close()
        except Exception as exc:  # launch failed (e.g. no browser installed)
            self._fatal = f"browser launch failed: {exc}"
            self._drain_with_error()

    def _context_kwargs(self) -> dict:
        """Optional outbound proxy for the browser context."""
        proxy = self._settings.scrape_proxy.strip()
        if not proxy:
            return {}
        u = urlparse(proxy)
        server = f"{u.scheme or 'http'}://{u.hostname}"
        if u.port:
            server += f":{u.port}"
        cfg = {"server": server}
        if u.username:
            cfg["username"] = u.username
        if u.password:
            cfg["password"] = u.password
        return {"proxy": cfg}

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
            self._ensure_cleared(page)

            # dismiss the promo banner so it can't intercept the TRACK click
            for sel in ("[class*=BannerReport_closeBtn]", "[class*=closeBtn]"):
                try:
                    page.click(sel, timeout=1200)
                    break
                except Exception:
                    pass

            page.fill(_TEXTAREA, number)
            return self._capture_for_number(page, number, timeout_ms)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _capture_for_number(self, page, number: str, timeout_ms: int) -> dict:
        """Click Track and return the restapi payload that matches `number`.

        The page can emit restapi responses for 17track's default *sample*
        shipment or stale lookups (especially on a slow, Cloudflare-throttled VPS,
        where they can win the race). We keep reading responses until one actually
        contains the requested number; if none arrives in time we fail loudly
        (→ honest "not found") instead of returning someone else's parcel.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        deadline = time.monotonic() + timeout_ms / 1000
        clicked = False
        while True:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining < 1500:
                break
            try:
                with page.expect_response(
                    lambda r: _RESTAPI_MARK in r.url and r.request.method == "POST",
                    timeout=remaining,
                ) as resp_info:
                    if not clicked:
                        page.locator(_TRACK_BTN).first.click(timeout=timeout_ms)
                        clicked = True
                resp = resp_info.value
            except PWTimeout:
                break
            if resp.status != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            if response_has_number(data, number):
                logger.info("scrape: captured matching tracking response for %s", number)
                return data
            logger.info(
                "scrape: ignoring response not for %s (got %s)",
                number, [s.get("number") for s in (data.get("shipments") or [])],
            )
        raise ProviderError(f"no tracking response for {number} (blocked or not found)")

    def _ensure_cleared(self, page) -> None:
        """Ensure we're past Cloudflare and the search box is present.

        The real browser usually clears the challenge on its own. If it doesn't
        and a solver is configured, solve the captured Turnstile and inject the
        token to complete the challenge.
        """
        full_ms = int(self._settings.scrape_timeout * 1000)

        # 1) Fast path — did the browser clear the challenge by itself?
        try:
            page.wait_for_selector(_TEXTAREA, timeout=12000)
            return
        except Exception:
            pass

        # 2) Still blocked. Without a solver, give it the remaining time then
        #    let the caller's flow fail if it never clears.
        if self._solver is None:
            page.wait_for_selector(_TEXTAREA, timeout=full_ms)
            return

        params = None
        try:
            params = page.evaluate("() => window.__cfChallenge || null")
        except Exception:
            params = None
        if not params:
            # No capturable Turnstile (some challenges don't use one).
            page.wait_for_selector(_TEXTAREA, timeout=full_ms)
            return

        logger.info("scrape: Cloudflare challenge detected — solving via %s", self._solver.name)
        try:
            token = self._solver.solve_turnstile(**params)
        except SolverError as exc:
            raise ProviderError(f"captcha solve failed: {exc}") from exc

        try:
            page.evaluate("(t) => { if (window.__tsCallback) window.__tsCallback(t); }", token)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"failed to inject captcha token: {exc}") from exc

        page.wait_for_selector(_TEXTAREA, timeout=full_ms)

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
        budget = self.settings.scrape_timeout + 20
        if self.settings.solver_enabled:
            budget += self.settings.solver_timeout  # solving a challenge takes time
        try:
            data = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=budget)
        except asyncio.TimeoutError as exc:
            raise ProviderError("scrape timed out") from exc
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"scrape failed: {exc}") from exc

        return parse_restapi_payload(data, number, carrier, self.settings.include_raw)
