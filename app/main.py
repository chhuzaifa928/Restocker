"""FastAPI service — routes, startup load, static dashboard (SRS Section 9)."""
from __future__ import annotations

import csv
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .config import STATIC_DIR, settings
from .gate import ROUTE_AUTO, ROUTE_BLOCK, ROUTE_ESCALATE, evaluate
from .llm import generate_rationale
from .models import AdjustRequest, Decision, InventoryItem, Metrics, Situation
from .store import Store

app = FastAPI(title="ReStock AI", version="1.0.0")

agent = Agent(settings)
store = Store()


def _read_seed() -> list[dict]:
    """Read the seed CSV into product dicts. Tolerates a missing file (NFR-1)."""
    products: list[dict] = []
    try:
        with open(settings.seed_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    products.append({
                        "product_id": row["product_id"],
                        "product_name": row["product_name"],
                        "inventory_level": float(row["inventory_level"]),
                        "demand_forecast": float(row["demand_forecast"]),
                        "unit_price": float(row["unit_price"]),
                    })
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return products


@app.middleware("http")
async def _no_store(request, call_next):
    """Prevent browsers caching the dashboard assets or API responses, so a
    fresh deploy (or a mid-demo edit) is always what the viewer sees."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.on_event("startup")
def _startup() -> None:
    agent.load()
    budget = agent.monthly_budget if agent.loaded else 0.0
    store.configure(
        initial_budget=budget,
        model_loaded=agent.loaded,
        llm_enabled=settings.llm_enabled,
    )
    store.seed_inventory(_read_seed())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _process(sit: Situation, *, execute: bool = True) -> Decision:
    """Encode -> decide -> gate -> (execute / escalate / block). SRS Section 3."""
    remaining = store.budget
    state = agent.encode_state(
        sit.inventory_level, sit.demand_forecast, sit.unit_price, remaining
    )
    action, confidence, visits, _q = agent.decide(state)
    quantity = agent.quantity_for(action, sit.demand_forecast)
    cost = round(quantity * sit.unit_price, 2)

    gate = evaluate(action, quantity, cost, confidence, visits, remaining, settings)

    decision = Decision(
        id=str(uuid.uuid4()),
        timestamp=_now(),
        product_id=sit.product_id,
        product_name=sit.product_name,
        state=state,
        action=action,
        quantity=quantity,
        unit_price=sit.unit_price,
        cost=cost,
        confidence=confidence,
        route=gate.route,
        reasons=gate.reasons,
        status="PENDING",
        budget_after=remaining,
    )

    store.note_decision(sit.product_id, action, gate.route)

    if gate.route == ROUTE_BLOCK:
        decision.status = "BLOCKED"
    elif gate.route == ROUTE_AUTO:
        if execute:
            store.spend(cost)
            store.add_stock(sit.product_id, quantity)  # replenish stock
        decision.status = "AUTO_APPROVED"
        decision.budget_after = store.budget
    else:  # ESCALATED — write a rationale and queue for a human.
        ctx = {
            "product_name": sit.product_name,
            "action": action,
            "quantity": quantity,
            "unit_price": sit.unit_price,
            "cost": cost,
            "remaining_budget": remaining,
            "state": state,
            "confidence": confidence,
            "reasons": gate.reasons,
        }
        text, source = await generate_rationale(ctx, settings)
        decision.rationale = text
        decision.rationale_source = source
        decision.status = "PENDING"

    store.add(decision)
    return decision


# ---- API (SRS Section 9) -------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    # Must be fast and independent of the model/LLM so platform checks pass.
    return {"status": "ok"}


@app.post("/api/run-batch", response_model=List[Decision])
async def run_batch() -> List[Decision]:
    if not agent.loaded:
        raise HTTPException(503, "Model not loaded")
    store.reset()  # fresh decisions, budget, and inventory for the run
    results: List[Decision] = []
    for p in store.inventory():
        sit = Situation(
            product_id=p["product_id"],
            product_name=p["product_name"],
            inventory_level=p["inventory_level"],
            demand_forecast=p["demand_forecast"],
            unit_price=p["unit_price"],
        )
        results.append(await _process(sit))
    return results


@app.post("/api/evaluate", response_model=Decision)
async def evaluate_one(sit: Situation) -> Decision:
    if not agent.loaded:
        raise HTTPException(503, "Model not loaded")
    return await _process(sit)


@app.get("/api/decisions", response_model=List[Decision])
def decisions(status: str | None = None) -> List[Decision]:
    return store.all(status)


@app.get("/api/pending", response_model=List[Decision])
def pending() -> List[Decision]:
    return store.pending()


@app.post("/api/decisions/{decision_id}/approve", response_model=Decision)
def approve(decision_id: str) -> Decision:
    d = store.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    if d.status != "PENDING":
        raise HTTPException(409, f"Decision already resolved ({d.status})")
    store.spend(d.cost)
    store.add_stock(d.product_id, d.quantity)  # replenish on human approval
    d.status = "HUMAN_APPROVED"
    d.human_decision = "APPROVED"
    d.budget_after = store.budget
    return d


@app.post("/api/decisions/{decision_id}/reject", response_model=Decision)
def reject(decision_id: str) -> Decision:
    d = store.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    if d.status != "PENDING":
        raise HTTPException(409, f"Decision already resolved ({d.status})")
    d.status = "HUMAN_REJECTED"
    d.human_decision = "REJECTED"
    return d


@app.get("/api/metrics", response_model=Metrics)
def metrics() -> Metrics:
    return store.metrics()


# ---- inventory management -----------------------------------------------
def _inventory_item(p: dict) -> InventoryItem:
    inv = p["inventory_level"]
    fc = p["demand_forecast"]
    coverage = (inv / fc) if fc > 0 else 0.0
    if coverage < 0.75:
        status = "LOW"
    elif coverage > 1.5:
        status = "HIGH"
    else:
        status = "HEALTHY"
    target = fc * 1.5 if fc > 0 else max(inv, 1.0)
    stock_pct = round(min(inv / target, 1.0) * 100, 1) if target else 0.0
    return InventoryItem(
        product_id=p["product_id"],
        product_name=p["product_name"],
        inventory_level=round(inv, 2),
        demand_forecast=fc,
        unit_price=p["unit_price"],
        coverage=round(coverage, 2),
        status=status,
        stock_pct=stock_pct,
        reorder_point=round(0.75 * fc, 1),
        last_action=p.get("last_action"),
        last_route=p.get("last_route"),
    )


@app.get("/api/inventory", response_model=List[InventoryItem])
def inventory() -> List[InventoryItem]:
    return [_inventory_item(p) for p in store.inventory()]


@app.post("/api/inventory/{product_id}/reorder", response_model=Decision)
async def reorder(product_id: str) -> Decision:
    """Evaluate a single product at its CURRENT stock level (a 'working' card)."""
    if not agent.loaded:
        raise HTTPException(503, "Model not loaded")
    p = store.get_product(product_id)
    if p is None:
        raise HTTPException(404, "Product not found")
    sit = Situation(
        product_id=p["product_id"],
        product_name=p["product_name"],
        inventory_level=p["inventory_level"],
        demand_forecast=p["demand_forecast"],
        unit_price=p["unit_price"],
    )
    return await _process(sit)


@app.post("/api/inventory/{product_id}/adjust", response_model=InventoryItem)
def adjust(product_id: str, req: AdjustRequest) -> InventoryItem:
    """Manual stock correction (receive shipment, shrinkage, stock-take)."""
    if req.delta is None and req.level is None:
        raise HTTPException(400, "Provide 'delta' or 'level'")
    updated = store.set_stock(product_id, delta=req.delta, level=req.level)
    if updated is None:
        raise HTTPException(404, "Product not found")
    return _inventory_item(updated)


@app.get("/api/model-status")
def model_status() -> dict:
    return {
        "loaded": agent.loaded,
        "error": agent.load_error,
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider if settings.llm_enabled else None,
        "constants": {
            "auto_limit": settings.auto_limit,
            "hard_limit": settings.hard_limit,
            "max_order_qty": settings.max_order_qty,
            "conf_threshold": settings.conf_threshold,
            "min_visits_familiar": settings.min_visits_familiar,
        },
    }


# ---- static dashboard ----------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
