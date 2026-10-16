"""Order fulfillment — the agent does real work, not just bookkeeping.

When an order is actually executed (an AUTO-APPROVED routine order, or an
escalation a human approved) this raises a **purchase order**: a PO number and
a receipt record. If ``PURCHASE_ORDER_WEBHOOK_URL`` is set, the receipt is also
POSTed to that endpoint — so the approved order leaves the app and lands in
whatever downstream system the operator wires up (a supplier intake, a Google
Sheet via Apps Script, a Zapier/n8n hook, …).

Like the LLM layer, this never raises to the caller: a webhook failure still
produces a valid local PO so the demo and the budget math stay consistent.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import httpx

from .config import Settings


def _po_number() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"PO-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def build_receipt(decision, po_number: str) -> Dict:
    """A supplier-facing purchase-order receipt built from a decision."""
    return {
        "po_number": po_number,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "product_id": decision.product_id,
        "product_name": decision.product_name,
        "quantity": decision.quantity,
        "unit_price": decision.unit_price,
        "total_cost": decision.cost,
        "approval": decision.status,          # AUTO_APPROVED | HUMAN_APPROVED
        "approved_by": "ai-agent" if decision.status == "AUTO_APPROVED" else "human",
    }


async def fulfill_order(decision, settings: Settings) -> Tuple[str, str]:
    """Issue a PO for an executed order. Returns (po_number, fulfillment) where
    fulfillment is 'local', 'webhook', or 'webhook_failed'. Mutates nothing."""
    po_number = _po_number()
    url: Optional[str] = settings.purchase_order_webhook_url
    if not url:
        return po_number, "local"

    receipt = build_receipt(decision, po_number)
    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
            resp = await client.post(url, json=receipt)
            resp.raise_for_status()
        return po_number, "webhook"
    except Exception:  # noqa: BLE001 - downstream hiccup must not void the order
        return po_number, "webhook_failed"
