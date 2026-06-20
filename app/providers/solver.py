"""Captcha / Cloudflare-challenge solver integration.

Currently supports 2Captcha. Used by the scrape provider as a fallback when a
real browser can't clear a Cloudflare challenge on its own: we capture the
Turnstile parameters from the page, hand them to the solver, and inject the
returned token.

These calls are synchronous (httpx) and run inside the scrape worker thread.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from ..config import Settings


class SolverError(Exception):
    """Raised when the solver can't produce a token."""


class TwoCaptchaSolver:
    name = "twocaptcha"
    BASE = "https://api.2captcha.com"

    def __init__(self, api_key: str, timeout: float = 180.0) -> None:
        self._key = api_key.strip()
        self._timeout = timeout

    def get_balance(self) -> float:
        with httpx.Client(timeout=20) as c:
            r = c.post(f"{self.BASE}/getBalance", json={"clientKey": self._key})
            d = r.json()
        if d.get("errorId"):
            raise SolverError(f"getBalance: {d.get('errorDescription')}")
        return float(d.get("balance") or 0)

    def solve_turnstile(
        self,
        website_url: str,
        website_key: str,
        action: Optional[str] = None,
        data: Optional[str] = None,
        pagedata: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        """Solve a Cloudflare Turnstile / Challenge and return the token."""
        task: dict = {
            "type": "TurnstileTaskProxyless",
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        # Extra fields are required for Cloudflare *Challenge* pages.
        if action:
            task["action"] = action
        if data:
            task["data"] = data
        if pagedata:
            task["pagedata"] = pagedata
        if user_agent:
            task["userAgent"] = user_agent

        with httpx.Client(timeout=30) as c:
            r = c.post(f"{self.BASE}/createTask", json={"clientKey": self._key, "task": task})
            d = r.json()
            if d.get("errorId"):
                raise SolverError(f"createTask: {d.get('errorDescription')}")
            task_id = d.get("taskId")
            if not task_id:
                raise SolverError("createTask returned no taskId")

            deadline = time.time() + self._timeout
            while time.time() < deadline:
                time.sleep(5)
                rr = c.post(f"{self.BASE}/getTaskResult", json={"clientKey": self._key, "taskId": task_id})
                dd = rr.json()
                if dd.get("errorId"):
                    raise SolverError(f"getTaskResult: {dd.get('errorDescription')}")
                if dd.get("status") == "ready":
                    token = (dd.get("solution") or {}).get("token")
                    if not token:
                        raise SolverError("solver returned no token")
                    return token
        raise SolverError("solver timed out")


def get_solver(settings: Settings):
    """Return a configured solver, or None when none is set up."""
    if not settings.solver_enabled:
        return None
    provider = settings.solver_provider.strip().lower()
    if provider == "twocaptcha":
        return TwoCaptchaSolver(settings.solver_api_key, settings.solver_timeout)
    # Unknown provider name → behave as if disabled (logged by the caller).
    return None
