"""
Generates the pre-trained artifacts the SRS expects:
  model/config.json      - reference medians, monthly budget (from "training")
  model/q_table.json     - full Q-table over the 81 discretised states
  data/inventory_seed.csv- ~10 situations crafted to hit AUTO / ESCALATE / BLOCK

There is no training notebook in this build, so this script synthesises a
coherent, explainable policy instead. The resulting q_table is loaded for
inference only; the app never modifies it.
"""
import csv
import json
import os

# Repo root (this file lives in <root>/scripts/).
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEVELS = ["LOW", "MEDIUM", "HIGH"]
ACTIONS = ["WAIT", "BUY_SMALL", "BUY_MEDIUM", "BUY_LARGE"]
MULT = {"WAIT": 0.0, "BUY_SMALL": 0.5, "BUY_MEDIUM": 1.0, "BUY_LARGE": 2.0}

# ---- config (what a notebook would export) -------------------------------
CONFIG = {
    "median_inventory": 100.0,
    "median_forecast": 150.0,
    "median_price": 20.0,
    "monthly_budget": 30000.0,
    "thresholds": {
        "ratio_low": 0.75,
        "ratio_high": 1.25,
        "budget_low": 0.33,
        "budget_high": 0.67,
    },
    "actions": ACTIONS,
    "action_multipliers": MULT,
    "note": "Synthesised policy config (no training notebook in this build).",
}

L = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def preferred_action(inv, fc, price, budget):
    """Explainable heuristic policy -> index into ACTIONS."""
    # High inventory -> we are well stocked, wait.
    if inv == "HIGH":
        return 0  # WAIT
    # Base buy size on forecast demand.
    size = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}[fc]  # SMALL/MEDIUM/LARGE
    # Medium inventory -> we have some stock, buy one size smaller.
    if inv == "MEDIUM":
        size -= 1
    # Expensive items or tight budget -> be cautious, one size smaller.
    if price == "HIGH" or budget == "LOW":
        size -= 1
    size = max(0, min(3, size))
    return size


def q_for_state(inv, fc, price, budget, decisive=True):
    """Build Q-values with a controllable decisiveness margin.

    Confident states: the preferred action dominates (high margin).
    Flat states: the runner-up is almost tied (low margin -> low confidence).
    """
    best = preferred_action(inv, fc, price, budget)
    # Rank the non-best actions by distance from the preferred one.
    others = sorted((a for a in range(4) if a != best), key=lambda a: abs(a - best))
    band = [3.0, 2.5, 2.0]  # nearest neighbour is clearly below best
    q = [0.0, 0.0, 0.0, 0.0]
    q[best] = 10.0
    for rank, a in enumerate(others):
        q[a] = band[rank]
    if not decisive:
        # Raise the nearest runner-up to near-tie -> small margin.
        q[others[0]] = 9.5
    q = [round(v, 2) for v in q]
    return q, ACTIONS[best]


def build_q_table(special):
    """`special` maps a state key to either an int (visit count, confident)
    or the string "flat" (near-tie Q-values, confident visit count)."""
    states = {}
    for inv in LEVELS:
        for fc in LEVELS:
            for price in LEVELS:
                for budget in LEVELS:
                    key = f"{inv}|{fc}|{price}|{budget}"
                    mode = special.get(key)
                    q, _ = q_for_state(inv, fc, price, budget, decisive=(mode != "flat"))
                    entry = {ACTIONS[i]: q[i] for i in range(4)}
                    entry["_visits"] = mode if isinstance(mode, int) else 28
                    states[key] = entry
    return {
        "meta": {"actions": ACTIONS, "visit_counts_included": True},
        "states": states,
    }


def level_for(value, median, cfg):
    ratio = value / median if median else 0.0
    if ratio < cfg["ratio_low"]:
        return "LOW"
    if ratio > cfg["ratio_high"]:
        return "HIGH"
    return "MEDIUM"


def budget_level(remaining, monthly, cfg):
    r = remaining / monthly if monthly else 0.0
    if r < cfg["budget_low"]:
        return "LOW"
    if r > cfg["budget_high"]:
        return "HIGH"
    return "MEDIUM"


# ---- seed products, crafted for the three routes -------------------------
# All encode against the start-of-run budget (HIGH), budget pool = monthly.
SEED = [
    # product_id, name, inventory, forecast, unit_price, intent
    ("SKU-1001", "Basmati Rice 5kg",        30, 120, 10.0, "AUTO"),
    ("SKU-1002", "White Sugar 1kg",         25,  90,  8.0, "AUTO"),
    ("SKU-1003", "All-Purpose Flour 10kg",  90, 150, 12.0, "AUTO"),
    ("SKU-1004", "Table Salt 1kg",         220, 100,  3.0, "AUTO-WAIT"),
    ("SKU-1005", "Sunflower Oil 5L",        35, 160, 14.0, "AUTO"),
    ("SKU-1006", "Olive Oil 1L",            20, 200, 22.0, "ESC-COST"),
    ("SKU-1007", "Imported Arabica Coffee", 45, 140, 26.0, "ESC-CONF"),
    ("SKU-1008", "Seasonal Mango Pulp",     30, 150, 19.0, "ESC-UNFAMILIAR"),
    ("SKU-1009", "Bulk Diesel Generator",   10, 250, 55.0, "BLOCK-COST"),
    ("SKU-1010", "Bottled Water 24pk",      20, 260,  6.0, "BLOCK-QTY"),
]


def main():
    cfg = CONFIG
    monthly = cfg["monthly_budget"]

    special = {}
    rows = []

    # First pass: compute each product's start state so we can tune the table.
    product_states = {}
    for pid, name, inv, fc, price, intent in SEED:
        inv_l = level_for(inv, cfg["median_inventory"], cfg["thresholds"])
        fc_l = level_for(fc, cfg["median_forecast"], cfg["thresholds"])
        pr_l = level_for(price, cfg["median_price"], cfg["thresholds"])
        bud_l = budget_level(monthly, monthly, cfg["thresholds"])  # start HIGH
        key = f"{inv_l}|{fc_l}|{pr_l}|{bud_l}"
        product_states[pid] = (key, intent, inv_l, fc_l, pr_l, bud_l)

    # Mark the unfamiliar demo state as low-visit; the low-confidence state as flat.
    for pid, (key, intent, *_rest) in product_states.items():
        if intent == "ESC-UNFAMILIAR":
            special[key] = 2          # < MIN_VISITS_FAMILIAR (5)
        if intent == "ESC-CONF":
            special[key] = "flat"     # near-tie Q-values -> low margin

    q_table = build_q_table(special)

    # Build CSV rows. remaining_budget column carries the start budget (global pool).
    for pid, name, inv, fc, price, intent in SEED:
        rows.append({
            "product_id": pid,
            "product_name": name,
            "inventory_level": inv,
            "demand_forecast": fc,
            "unit_price": price,
            "remaining_budget": int(monthly),
        })

    os.makedirs(os.path.join(OUT, "model"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "data"), exist_ok=True)

    with open(os.path.join(OUT, "model", "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    with open(os.path.join(OUT, "model", "q_table.json"), "w", encoding="utf-8") as f:
        json.dump(q_table, f, indent=2)
    with open(os.path.join(OUT, "data", "inventory_seed.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- sanity print: predict routes the way the app will --------------
    AUTO_LIMIT, HARD_LIMIT, MAX_QTY, CONF_T, MIN_V = 5000.0, 12000.0, 400, 0.60, 5
    print(f"{'product':28} {'state':22} {'action':11} {'qty':>5} {'cost':>9} {'conf':>5}  route")
    remaining = monthly
    for pid, name, inv, fc, price, intent in SEED:
        key, _intent, *_ = product_states[pid]
        entry = q_table["states"][key]
        qvals = {a: entry[a] for a in ACTIONS}
        best = max(qvals, key=qvals.get)
        vals = list(qvals.values())
        q_best = max(vals)
        q_second = sorted(vals, reverse=True)[1]
        spread = max(vals) - min(vals)
        margin = 0.0 if spread <= 0 else (q_best - q_second) / spread
        visits = entry.get("_visits", 0)
        familiarity = min(visits / (2 * MIN_V), 1.0)
        conf = 0.7 * margin + 0.3 * familiarity
        qty = round(MULT[best] * max(fc, 0))
        cost = qty * price
        # gate
        if qty > MAX_QTY or cost > HARD_LIMIT or cost > remaining:
            route = "BLOCKED"
        elif best == "WAIT":
            route = "AUTO"
        elif cost > AUTO_LIMIT:
            route = "ESCALATED(cost)"
        elif conf < CONF_T:
            route = "ESCALATED(conf)"
        elif visits < MIN_V:
            route = "ESCALATED(unfamiliar)"
        else:
            route = "AUTO"
            remaining -= cost
        print(f"{name:28} {key:22} {best:11} {qty:5d} {cost:9.0f} {conf:5.2f}  {route}  [{intent}]")


if __name__ == "__main__":
    main()
