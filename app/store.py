"""In-memory decision store (SRS 5.2 — in-memory is acceptable for the MVP).

Holds every decision record plus the single global budget pool. The pool is
initialised from the training config's monthly budget and decrements as orders
execute. `reset()` makes `POST /api/run-batch` idempotent for the live demo.
"""
from __future__ import annotations

import copy
import threading
from typing import Dict, List, Optional

from .models import Decision, Metrics


class Store:
    def __init__(self, initial_budget: float = 0.0):
        self._lock = threading.Lock()
        self._decisions: List[Decision] = []
        self._by_id: Dict[str, Decision] = {}
        self.initial_budget = initial_budget
        self.remaining_budget = initial_budget
        self.model_loaded = False
        self.llm_enabled = False
        # Live inventory (inventory management).
        self._order: List[str] = []              # product ids in seed order
        self._inv: Dict[str, dict] = {}          # current stock per product
        self._inv_seed: Dict[str, dict] = {}     # pristine copy for reset
        self.units_replenished = 0

    def configure(self, initial_budget: float, model_loaded: bool, llm_enabled: bool) -> None:
        with self._lock:
            self.initial_budget = initial_budget
            self.remaining_budget = initial_budget
            self.model_loaded = model_loaded
            self.llm_enabled = llm_enabled

    def reset(self) -> None:
        with self._lock:
            self._decisions.clear()
            self._by_id.clear()
            self.remaining_budget = self.initial_budget
            self._inv = copy.deepcopy(self._inv_seed)
            self.units_replenished = 0

    # ---- inventory -------------------------------------------------------
    def seed_inventory(self, products: List[dict]) -> None:
        """products: dicts with product_id, product_name, inventory_level,
        demand_forecast, unit_price."""
        with self._lock:
            self._order = []
            self._inv_seed = {}
            for p in products:
                pid = p["product_id"]
                self._order.append(pid)
                self._inv_seed[pid] = {
                    "product_id": pid,
                    "product_name": p["product_name"],
                    "inventory_level": float(p["inventory_level"]),
                    "demand_forecast": float(p["demand_forecast"]),
                    "unit_price": float(p["unit_price"]),
                    "last_action": None,
                    "last_route": None,
                }
            self._inv = copy.deepcopy(self._inv_seed)
            self.units_replenished = 0

    def get_product(self, pid: str) -> Optional[dict]:
        return self._inv.get(pid)

    def inventory(self) -> List[dict]:
        return [self._inv[pid] for pid in self._order if pid in self._inv]

    def note_decision(self, pid: str, action: str, route: str) -> None:
        with self._lock:
            if pid in self._inv:
                self._inv[pid]["last_action"] = action
                self._inv[pid]["last_route"] = route

    def add_stock(self, pid: str, qty: float) -> None:
        with self._lock:
            if pid in self._inv:
                self._inv[pid]["inventory_level"] += qty
                self.units_replenished += int(qty)

    def set_stock(self, pid: str, *, delta: Optional[float] = None,
                  level: Optional[float] = None) -> Optional[dict]:
        with self._lock:
            if pid not in self._inv:
                return None
            if level is not None:
                self._inv[pid]["inventory_level"] = max(0.0, float(level))
            elif delta is not None:
                cur = self._inv[pid]["inventory_level"]
                self._inv[pid]["inventory_level"] = max(0.0, cur + float(delta))
            return self._inv[pid]

    # ---- budget ----------------------------------------------------------
    @property
    def budget(self) -> float:
        return self.remaining_budget

    def spend(self, amount: float) -> None:
        with self._lock:
            self.remaining_budget -= amount

    def refund(self, amount: float) -> None:
        with self._lock:
            self.remaining_budget += amount

    # ---- records ---------------------------------------------------------
    def add(self, decision: Decision) -> None:
        with self._lock:
            self._decisions.append(decision)
            self._by_id[decision.id] = decision

    def get(self, decision_id: str) -> Optional[Decision]:
        return self._by_id.get(decision_id)

    def all(self, status: Optional[str] = None) -> List[Decision]:
        items = list(reversed(self._decisions))  # newest first
        if status:
            items = [d for d in items if d.status == status]
        return items

    def pending(self) -> List[Decision]:
        return [d for d in reversed(self._decisions) if d.status == "PENDING"]

    # ---- metrics (SRS 7.2 dashboard) ------------------------------------
    def metrics(self) -> Metrics:
        total = len(self._decisions)
        auto = sum(1 for d in self._decisions if d.status == "AUTO_APPROVED")
        human_ok = sum(1 for d in self._decisions if d.status == "HUMAN_APPROVED")
        human_no = sum(1 for d in self._decisions if d.status == "HUMAN_REJECTED")
        pending = sum(1 for d in self._decisions if d.status == "PENDING")
        blocked = sum(1 for d in self._decisions if d.status == "BLOCKED")
        escalated = pending + human_ok + human_no
        spend = sum(
            d.cost for d in self._decisions
            if d.status in ("AUTO_APPROVED", "HUMAN_APPROVED")
        )
        low_stock = sum(
            1 for p in self._inv.values()
            if p["demand_forecast"] > 0
            and p["inventory_level"] / p["demand_forecast"] < 0.75
        )
        return Metrics(
            total_decisions=total,
            auto_approved=auto,
            auto_approved_pct=round(100.0 * auto / total, 1) if total else 0.0,
            escalated=escalated,
            pending=pending,
            blocked=blocked,
            human_approved=human_ok,
            human_rejected=human_no,
            total_spend=round(spend, 2),
            budget_remaining=round(self.remaining_budget, 2),
            initial_budget=round(self.initial_budget, 2),
            products_tracked=len(self._inv),
            low_stock=low_stock,
            units_replenished=self.units_replenished,
            model_loaded=self.model_loaded,
            llm_enabled=self.llm_enabled,
        )
