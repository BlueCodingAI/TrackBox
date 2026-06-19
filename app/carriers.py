"""Carrier code ↔ metadata lookup, sourced from 17track's public carrier list.

The data file `data/carriers.json` is a compact map:
    { "7041": {"name": "DHL Paket", "country": "DE", "url": "https://..."} }
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA_FILE = Path(__file__).parent / "data" / "carriers.json"


@lru_cache
def _carriers() -> dict[str, dict]:
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def carrier_info(code: Optional[int | str]) -> Optional[dict]:
    """Return {name, country, url} for a carrier code, or None if unknown."""
    if code is None:
        return None
    entry = _carriers().get(str(code))
    if not entry:
        return None
    return {
        "code": _coerce_int(code),
        "name": entry.get("name"),
        "country": entry.get("country"),
        "url": entry.get("url"),
    }


def carrier_name(code: Optional[int | str]) -> Optional[str]:
    info = carrier_info(code)
    return info["name"] if info else None


def search_carriers(query: str, limit: int = 20) -> list[dict]:
    """Case-insensitive name search, useful for a carrier picker in the UI."""
    q = (query or "").strip().lower()
    matches: list[dict] = []
    for code, entry in _carriers().items():
        name = entry.get("name") or ""
        if not q or q in name.lower():
            matches.append(
                {
                    "code": int(code),
                    "name": name,
                    "country": entry.get("country"),
                    "url": entry.get("url"),
                }
            )
    # Sort the full match set first, THEN slice — otherwise we'd return the
    # alphabetized first-N-in-file-order rather than the alphabetically-first N.
    matches.sort(key=lambda c: c["name"].lower())
    return matches[:limit]


def _coerce_int(code: int | str) -> Optional[int]:
    try:
        return int(code)
    except (TypeError, ValueError):
        return None
