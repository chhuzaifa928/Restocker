"""Decision store — in-memory source of truth, mirrored to SQLite.

Holds every decision record plus the single global budget pool. The pool is
initialised from the training config's monthly budget and decrements as orders
execute. `reset()` makes `POST /api/run-batch` idempotent for the live demo.

On top of the MVP in-memory model this adds:
  * **Persistence** — every mutation mirrors to SQLite (:mod:`app.db`) so state
    survives a container restart; on startup the store rehydrates from it.
  * **Human-feedback loop** — approvals/rejections of escalations are recorded
    as agreement/override, and a run of rejections raises the *effective*
    confidence threshold so the gate escalates more cautiously. This adjusts
    the governance gate only; it never touches the pre-trained Q-table.
"""
from __future__ import annotations

import copy
import threading
from typing import Dict, List, Optional

from .db import DB
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
        # Human-feedback / adaptive gate.
        self.base_conf_threshold = 0.60
        self.effective_conf_threshold = 0.60
        self.feedback_events = 0                 # total human decisions recorded
        # Persistence (set in configure()).
        self.db: Optional[DB] = None

    # ---- configuration & rehydration ------------------------------------
    def configure(self, initial_budget: float, model_loaded: bool,
                  llm_enabled: bool, base_conf_threshold: float = 0.60,
                  db: Optional[DB] = None) -> None:
        with self._lock:
            self.initial_budget = initial_budget
            self.remaining_budget = initial_budget
            self.model_loaded = model_loaded
            self.llm_enabled = llm_enabled
            self.base_conf_threshold = base_conf_threshold
            self.effective_conf_threshold = base_conf_threshold
            self.db = db

    def rehydrate(self) -> bool:
        """Restore decisions, budget and feedback from the DB. Returns True if
        any persisted state was found. Inventory is restored separately so the
        CSV seed can still prime a first-run database."""
        if not self.db or not self.db.ok:
            return False
        rows = self.db.load_decisions()
        found = False
        with self._lock:
            if rows:
                found = True
                self._decisions = [Decision(**r) for r in rows]
                # Preserve insertion order by timestamp then id for stability.
                self._decisions.sort(key=lambda d: d.timestamp)
                self._by_id = {d.id: d for d in self._decisions}
            self.remaining_budget = self.db.get_meta("remaining_budget", self.remaining_budget)
            self.units_replenished = self.db.get_meta("units_replenished", self.units_replenished)
            self.effective_conf_threshold = self.db.get_meta(
                "effective_conf_threshold", self.effective_conf_threshold)
            self.feedback_events = self.db.get_meta("feedback_events", self.feedback_events)
            order, inv = self.db.load_inventory()
            if order and inv:
                found = True
                self._order = order
                self._inv = inv
        return found

    def _persist_meta(self) -> None:
        if self.db and self.db.ok:
            self.db.set_meta("remaining_budget", self.remaining_budget)
            self.db.set_meta("units_replenished", self.units_replenished)
            self.db.set_meta("effective_conf_threshold", self.effective_conf_threshold)
            self.db.set_meta("feedback_events", self.feedback_events)

    def reset(self) -> None:
        with self._lock:
            self._decisions.clear()
            self._by_id.clear()
            self.remaining_budget = self.initial_budget
            self._inv = copy.deepcopy(self._inv_seed)
            self.units_replenished = 0
            self.effective_conf_threshold = self.base_conf_threshold
            self.feedback_events = 0
            if self.db and self.db.ok:
                self.db.clear_decisions()
                self.db.save_inventory(self._order, self._inv)
                self._persist_meta()

    # ---- inventory -------------------------------------------------------
    def seed_inventory(self, products: List[dict]) -> None:
        """products: dicts with product_id, product_name, inventory_level,
        demand_forecast, unit_price. Always (re)builds the pristine seed; only
        overwrites live stock when it has not been restored from the DB."""
        with self._lock:
            had_live = bool(self._inv)
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
            if not had_live:
                self._inv = copy.deepcopy(self._inv_seed)
                self.units_replenished = 0
                if self.db and self.db.ok:
                    self.db.save_inventory(self._order, self._inv)

    def get_product(self, pid: str) -> Optional[dict]:
        return self._inv.get(pid)

    def inventory(self) -> List[dict]:
        return [self._inv[pid] for pid in self._order if pid in self._inv]

    def note_decision(self, pid: str, action: str, route: str) -> None:
        with self._lock:
            if pid in self._inv:
                self._inv[pid]["last_action"] = action
                self._inv[pid]["last_route"] = route
                self._persist_product(pid)

    def add_stock(self, pid: str, qty: float) -> None:
        with self._lock:
            if pid in self._inv:
                self._inv[pid]["inventory_level"] += qty
                self.units_replenished += int(qty)
                self._persist_product(pid)
                if self.db and self.db.ok:
                    self.db.set_meta("units_replenished", self.units_replenished)

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
            self._persist_product(pid)
            return self._inv[pid]

    def _persist_product(self, pid: str) -> None:
        if self.db and self.db.ok and pid in self._order:
            self.db.save_product(pid, self._order.index(pid), self._inv[pid])

    # ---- budget ----------------------------------------------------------
    @property
    def budget(self) -> float:
        return self.remaining_budget

    def spend(self, amount: float) -> None:
        with self._lock:
            self.remaining_budget -= amount
            if self.db and self.db.ok:
                self.db.set_meta("remaining_budget", self.remaining_budget)

    def refund(self, amount: float) -> None:
        with self._lock:
            self.remaining_budget += amount
            if self.db and self.db.ok:
                self.db.set_meta("remaining_budget", self.remaining_budget)

    # ---- human-feedback loop --------------------------------------------
    def record_feedback(self, agreed: bool) -> None:
        """A human resolved an escalation. `agreed=True` (approved the AI's
        recommended order) nudges the effective threshold back toward base;
        `agreed=False` (rejected it — an override) raises the threshold so the
        gate escalates sooner next time. Governance only; the Q-table is never
        modified."""
        with self._lock:
            self.feedback_events += 1
            if agreed:
                self.effective_conf_threshold = max(
                    self.base_conf_threshold, self.effective_conf_threshold - 0.02)
            else:
                self.effective_conf_threshold = min(
                    0.90, round(self.effective_conf_threshold + 0.05, 4))
            if self.db and self.db.ok:
                self.db.set_meta("effective_conf_threshold", self.effective_conf_threshold)
                self.db.set_meta("feedback_events", self.feedback_events)

    # ---- records ---------------------------------------------------------
    def add(self, decision: Decision) -> None:
        with self._lock:
            self._decisions.append(decision)
            self._by_id[decision.id] = decision
            if self.db and self.db.ok:
                self.db.save_decision(decision.id, decision.model_dump())

    def persist(self, decision: Decision) -> None:
        """Re-save a decision after it was mutated in place (e.g. approved)."""
        if self.db and self.db.ok:
            self.db.save_decision(decision.id, decision.model_dump())

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
        orders_filled = sum(1 for d in self._decisions if d.po_number)
        resolved = human_ok + human_no
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
            orders_filled=orders_filled,
            agreement_rate=round(100.0 * human_ok / resolved, 1) if resolved else 0.0,
            override_rate=round(100.0 * human_no / resolved, 1) if resolved else 0.0,
            effective_conf_threshold=round(self.effective_conf_threshold, 4),
            base_conf_threshold=round(self.base_conf_threshold, 4),
            model_loaded=self.model_loaded,
            llm_enabled=self.llm_enabled,
        )
