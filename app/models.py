"""Pydantic schemas (SRS Section 5 data model + Section 9 API shapes)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Situation(BaseModel):
    """One purchasing situation (a CSV row or a manual UI submission)."""

    product_id: str
    product_name: str
    inventory_level: float = Field(..., ge=0)
    demand_forecast: float = Field(..., ge=0)
    unit_price: float = Field(..., gt=0)
    # Optional on manual input; the app uses its own global budget pool.
    remaining_budget: Optional[float] = Field(default=None, ge=0)


class Decision(BaseModel):
    """A fully-routed decision record (SRS 5.2)."""

    id: str
    timestamp: str
    product_id: str
    product_name: str
    state: List[str]
    action: str
    quantity: int
    unit_price: float
    cost: float
    confidence: float
    confidence_margin: float = 0.0        # how decisively the agent preferred its action
    confidence_familiarity: float = 0.0   # how well-trained that state is
    route: str                       # AUTO_APPROVED | ESCALATED | BLOCKED
    reasons: List[str] = []
    rationale: str = ""
    rationale_source: str = ""       # "llm" | "fallback" | ""
    status: str                      # PENDING | AUTO_APPROVED | HUMAN_APPROVED | HUMAN_REJECTED | BLOCKED
    human_decision: Optional[str] = None  # None | APPROVED | REJECTED
    budget_after: float = 0.0
    po_number: Optional[str] = None       # set once an order is actually fulfilled
    fulfillment: str = ""                 # "" | "local" | "webhook" | "webhook_failed"
    notified: bool = False                # escalation pushed to Discord?


class InventoryItem(BaseModel):
    """A product's live stock position (inventory management view)."""

    product_id: str
    product_name: str
    inventory_level: float
    demand_forecast: float
    unit_price: float
    coverage: float            # inventory / forecast
    status: str                # LOW | HEALTHY | HIGH
    stock_pct: float           # 0-100, for the stock bar
    reorder_point: float
    last_action: Optional[str] = None
    last_route: Optional[str] = None


class AdjustRequest(BaseModel):
    """Manual stock correction from the inventory UI."""

    delta: Optional[float] = None   # relative change (+/-)
    level: Optional[float] = None   # or an absolute new level


class GateSettings(BaseModel):
    """Runtime edits to the dual-gate policy (Settings page). All optional."""

    auto_limit: Optional[float] = Field(default=None, ge=0)
    hard_limit: Optional[float] = Field(default=None, ge=0)
    max_order_qty: Optional[int] = Field(default=None, ge=1)
    conf_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    min_visits_familiar: Optional[int] = Field(default=None, ge=0)


class Metrics(BaseModel):
    total_decisions: int
    auto_approved: int
    auto_approved_pct: float
    escalated: int
    pending: int
    blocked: int
    human_approved: int
    human_rejected: int
    total_spend: float
    budget_remaining: float
    initial_budget: float
    products_tracked: int = 0
    low_stock: int = 0
    units_replenished: int = 0
    orders_filled: int = 0
    agreement_rate: float = 0.0          # % of resolved escalations the human approved
    override_rate: float = 0.0           # % the human rejected (overrode the AI)
    effective_conf_threshold: float = 0.60
    base_conf_threshold: float = 0.60
    model_loaded: bool
    llm_enabled: bool
