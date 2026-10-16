"""Escalation notifications — the human-in-the-loop leaves the dashboard.

When the gate escalates an order, this pushes a rich card to a **Discord**
webhook (Slack-compatible payloads are also supported) containing the AI's
plain-English rationale and two one-click links back to the service:

    ✅ Approve → GET  {PUBLIC_BASE_URL}/a/{id}
    ❌ Reject  → GET  {PUBLIC_BASE_URL}/r/{id}

So an operations manager can approve a purchase order straight from Discord on
their phone — exactly the "manager approves on Slack" pattern. If no webhook is
configured (or the post fails) the app simply relies on the on-screen approval
queue; this function never raises.
"""
from __future__ import annotations

from typing import Optional

import httpx

from .config import Settings
from .gate import REASON_LABELS

ROUTE_COLORS = {"ESCALATED": 0xF5A623}  # amber


def _reason_lines(reasons) -> str:
    return "\n".join(f"• {REASON_LABELS.get(r, r)}" for r in reasons) or "• Policy check"


def _links(decision_id: str, base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    return (
        f"[✅ Approve]({base}/a/{decision_id})  |  "
        f"[❌ Reject]({base}/r/{decision_id})"
    )


def _discord_payload(decision, base_url: str) -> dict:
    fields = [
        {"name": "Action", "value": f"{decision.action} · {decision.quantity} units", "inline": True},
        {"name": "Cost", "value": f"${decision.cost:,.0f}", "inline": True},
        {"name": "Confidence", "value": f"{decision.confidence * 100:.0f}%", "inline": True},
        {"name": "Why escalated", "value": _reason_lines(decision.reasons), "inline": False},
        {"name": "AI rationale", "value": decision.rationale[:1000] or "—", "inline": False},
    ]
    if base_url:
        fields.append({"name": "Decide", "value": _links(decision.id, base_url), "inline": False})
    return {
        "username": "ReStock AI",
        "embeds": [{
            "title": f"🛎️ Approval needed — {decision.product_name}",
            "description": "An order needs a human sign-off before it executes.",
            "color": ROUTE_COLORS.get(decision.route, 0x8892B0),
            "fields": fields,
            "footer": {"text": f"Decision {decision.id[:8]} · ReStock AI"},
        }],
    }


async def notify_escalation(decision, settings: Settings,
                            base_url: Optional[str] = None) -> bool:
    """Post an escalation to the configured webhook. Returns True on success."""
    url = settings.notify_discord_webhook_url
    if not url:
        return False
    base = base_url or settings.public_base_url
    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
            resp = await client.post(url, json=_discord_payload(decision, base))
            resp.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 - notifications are best-effort
        return False
