"""The dual gate — decision routing (SRS Section 6).

Evaluation order (first category that matches wins):
  1. BLOCK   if quantity > MAX_ORDER_QTY or cost > HARD_LIMIT or cost > budget
  2. WAIT actions (cost 0) always AUTO-APPROVE — a safe no-op
  3. ESCALATE if cost > AUTO_LIMIT               (cost_over_limit)
  4. ESCALATE if confidence < CONF_THRESHOLD     (low_confidence)
  5. ESCALATE if visits < MIN_VISITS_FAMILIAR    (unfamiliar_state)
  6. otherwise AUTO-APPROVE

All applicable escalation reasons are collected, even though any one triggers
escalation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .config import Settings

ROUTE_AUTO = "AUTO_APPROVED"
ROUTE_ESCALATE = "ESCALATED"
ROUTE_BLOCK = "BLOCKED"

REASON_LABELS = {
    "qty_over_limit": "Quantity over hard cap",
    "cost_over_hard_limit": "Cost over absolute ceiling",
    "over_budget": "Exceeds remaining budget",
    "cost_over_limit": "Over auto-approval limit",
    "low_confidence": "Low confidence",
    "unfamiliar_state": "Unfamiliar situation",
}


@dataclass
class GateResult:
    route: str
    reasons: List[str]


def evaluate(action: str, quantity: int, cost: float, confidence: float,
             visits: int, remaining_budget: float, settings: Settings,
             conf_threshold: float | None = None) -> GateResult:
    # The human-feedback loop can raise the confidence bar above the configured
    # default; fall back to the static setting when no override is supplied.
    conf_threshold = settings.conf_threshold if conf_threshold is None else conf_threshold
    # 1. BLOCK — hard safety limits.
    block_reasons: List[str] = []
    if quantity > settings.max_order_qty:
        block_reasons.append("qty_over_limit")
    if cost > settings.hard_limit:
        block_reasons.append("cost_over_hard_limit")
    if cost > remaining_budget:
        block_reasons.append("over_budget")
    if block_reasons:
        return GateResult(ROUTE_BLOCK, block_reasons)

    # 2. WAIT is always a safe auto-approve (no spend).
    if action == "WAIT" or cost <= 0:
        return GateResult(ROUTE_AUTO, [])

    # 3-5. Collect every applicable escalation reason.
    reasons: List[str] = []
    if cost > settings.auto_limit:
        reasons.append("cost_over_limit")
    if confidence < conf_threshold:
        reasons.append("low_confidence")
    if visits < settings.min_visits_familiar:
        reasons.append("unfamiliar_state")

    if reasons:
        return GateResult(ROUTE_ESCALATE, reasons)

    # 6. Routine, confident, familiar, affordable.
    return GateResult(ROUTE_AUTO, [])
