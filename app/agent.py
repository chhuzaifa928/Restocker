"""Q-Learning agent — inference only (SRS Sections 3, 6.3, 7).

Loads a pre-trained Q-table and a config of training-time medians, then:
  * encodes a situation into a 4-level state,
  * selects argmax action (epsilon = 0, deterministic),
  * computes order quantity, cost, and a confidence score.

It never modifies the Q-table.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from .config import Settings

ACTIONS = ["WAIT", "BUY_SMALL", "BUY_MEDIUM", "BUY_LARGE"]
MULTIPLIERS = {"WAIT": 0.0, "BUY_SMALL": 0.5, "BUY_MEDIUM": 1.0, "BUY_LARGE": 2.0}


class AgentError(RuntimeError):
    pass


class Agent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.loaded = False
        self.load_error: Optional[str] = None
        self.q_table: Dict[str, dict] = {}
        self.actions: List[str] = ACTIONS
        self.multipliers: Dict[str, float] = MULTIPLIERS
        self.visit_counts_included = False
        # Encoding config (from training notebook export).
        self.median_inventory = 1.0
        self.median_forecast = 1.0
        self.median_price = 1.0
        self.monthly_budget = 0.0
        self.ratio_low = 0.75
        self.ratio_high = 1.25
        self.budget_low = 0.33
        self.budget_high = 0.67

    # ---- loading ---------------------------------------------------------
    def load(self) -> None:
        """Load config + q_table. Records an error instead of crashing (NFR-1)."""
        try:
            with open(self.settings.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.median_inventory = float(cfg["median_inventory"])
            self.median_forecast = float(cfg["median_forecast"])
            self.median_price = float(cfg["median_price"])
            self.monthly_budget = float(cfg["monthly_budget"])
            t = cfg.get("thresholds", {})
            self.ratio_low = float(t.get("ratio_low", 0.75))
            self.ratio_high = float(t.get("ratio_high", 1.25))
            self.budget_low = float(t.get("budget_low", 0.33))
            self.budget_high = float(t.get("budget_high", 0.67))
            if cfg.get("action_multipliers"):
                self.multipliers = {k: float(v) for k, v in cfg["action_multipliers"].items()}

            with open(self.settings.q_table_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("meta", {})
            self.actions = meta.get("actions", ACTIONS)
            self.visit_counts_included = bool(meta.get("visit_counts_included", False))
            self.q_table = data["states"]
            if not self.q_table:
                raise AgentError("q_table has no states")
            self.loaded = True
            self.load_error = None
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            self.loaded = False
            self.load_error = f"{type(exc).__name__}: {exc}"

    # ---- state encoding (SRS 7.2) ---------------------------------------
    def _ratio_level(self, value: float, median: float) -> str:
        ratio = value / median if median else 0.0
        if ratio < self.ratio_low:
            return "LOW"
        if ratio > self.ratio_high:
            return "HIGH"
        return "MEDIUM"

    def _budget_level(self, remaining: float) -> str:
        r = remaining / self.monthly_budget if self.monthly_budget else 0.0
        if r < self.budget_low:
            return "LOW"
        if r > self.budget_high:
            return "HIGH"
        return "MEDIUM"

    def encode_state(self, inventory: float, forecast: float, price: float,
                     remaining_budget: float) -> List[str]:
        return [
            self._ratio_level(inventory, self.median_inventory),
            self._ratio_level(forecast, self.median_forecast),
            self._ratio_level(price, self.median_price),
            self._budget_level(remaining_budget),
        ]

    # ---- decision + confidence (SRS 6.3) --------------------------------
    def decide(self, state: List[str]) -> Tuple[str, float, int, Dict[str, float]]:
        """Return (action, confidence, visits, q_values)."""
        key = "|".join(state)
        entry = self.q_table.get(key)

        if entry is None:
            # Unseen state: do nothing rather than guess (SRS 6.3 edge case).
            return "WAIT", 0.0, 0, {a: 0.0 for a in self.actions}

        q_values = {a: float(entry.get(a, 0.0)) for a in self.actions}
        visits = int(entry.get("_visits", 0)) if self.visit_counts_included else None

        action = max(q_values, key=q_values.get)  # argmax, epsilon = 0

        vals = list(q_values.values())
        q_best = max(vals)
        q_second = sorted(vals, reverse=True)[1] if len(vals) > 1 else q_best
        spread = max(vals) - min(vals)
        margin = 0.0 if spread <= 0 else (q_best - q_second) / spread

        if self.visit_counts_included and visits is not None:
            familiarity = min(visits / (2 * self.settings.min_visits_familiar), 1.0)
        else:
            # Visit counts unavailable: present in table => familiar.
            familiarity = 1.0
            visits = self.settings.min_visits_familiar  # treat as "familiar"

        confidence = 0.7 * margin + 0.3 * familiarity
        return action, round(confidence, 4), int(visits), q_values

    def quantity_for(self, action: str, forecast: float) -> int:
        mult = self.multipliers.get(action, 0.0)
        return int(round(mult * max(forecast, 0.0)))
