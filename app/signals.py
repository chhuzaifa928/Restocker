"""Demand-signal ingestion — the agent reacts to the outside world.

Most of the dashboard is operator-driven (click "Run batch", adjust a stock
level). This closes the loop the other way: a *demand signal* arrives from an
external source and the agent re-evaluates the affected products on its own —
auto-executing the routine reorders and escalating the risky ones, exactly as
it would for any other situation.

Source of truth:
  * If ``DEMAND_SIGNAL_URL`` is set, GET it and expect JSON like
    ``[{"product_id": "SKU-1006", "demand_forecast": 320}, ...]``.
  * Otherwise a built-in **mock sales feed** perturbs a few products' forecasts,
    so the reactive loop is demonstrable with zero configuration.

Never raises: a missing or broken endpoint falls back to the mock feed.
"""
from __future__ import annotations

import random
from typing import Dict, List

import httpx

from .config import Settings

# Deterministic-ish variety: a rotating set of demand multipliers so repeated
# ingests visibly change the picture (a spike, then a dip, …).
_FACTORS = [1.8, 0.6, 1.4, 0.75, 2.0, 1.25]


async def _from_url(url: str, timeout: float) -> List[Dict]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    out: List[Dict] = []
    for row in data:
        try:
            out.append({
                "product_id": str(row["product_id"]),
                "demand_forecast": float(row["demand_forecast"]),
                "source": "external",
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _mock_feed(products: List[dict], k: int = 3) -> List[Dict]:
    if not products:
        return []
    sample = random.sample(products, min(k, len(products)))
    signals: List[Dict] = []
    for p in sample:
        factor = random.choice(_FACTORS)
        new_fc = max(1.0, round(float(p["demand_forecast"]) * factor))
        signals.append({
            "product_id": p["product_id"],
            "product_name": p["product_name"],
            "demand_forecast": new_fc,
            "source": "mock-feed",
        })
    return signals


async def fetch_demand_signals(products: List[dict], settings: Settings) -> List[Dict]:
    """Return a list of {product_id, demand_forecast, source} updates."""
    if settings.demand_signal_url:
        try:
            signals = await _from_url(settings.demand_signal_url, settings.webhook_timeout)
            if signals:
                # Attach product_name for nicer UI messages where we can.
                names = {p["product_id"]: p["product_name"] for p in products}
                for s in signals:
                    s.setdefault("product_name", names.get(s["product_id"], s["product_id"]))
                return signals
        except Exception:  # noqa: BLE001 - fall back to the mock feed
            pass
    return _mock_feed(products)
