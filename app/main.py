"""FastAPI service — routes, startup load, static dashboard (SRS Section 9).

Beyond the MVP this wires four integrations around the same Q-table core:
  * SQLite persistence (state survives a redeploy),
  * real order fulfillment (approved orders raise a purchase order),
  * Discord escalation notifications with one-click Approve/Reject links,
  * a human-feedback loop that adapts the governance gate.
"""
from __future__ import annotations

import csv
import io
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .config import STATIC_DIR, settings
from .db import DB
from .fulfill import fulfill_order
from .gate import ROUTE_AUTO, ROUTE_BLOCK, ROUTE_ESCALATE, evaluate
from .llm import generate_rationale
from .models import AdjustRequest, Decision, InventoryItem, Metrics, Situation
from .notify import notify_escalation
from .store import Store

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


def _startup() -> None:
    agent.load()
    budget = agent.monthly_budget if agent.loaded else 0.0
    db = DB(settings.db_path) if settings.persistence_enabled else None
    store.configure(
        initial_budget=budget,
        model_loaded=agent.loaded,
        llm_enabled=settings.llm_enabled,
        base_conf_threshold=settings.conf_threshold,
        db=db,
    )
    store.rehydrate()                 # restore any persisted decisions/budget
    store.seed_inventory(_read_seed())  # primes inventory only on a fresh DB


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup()
    yield


app = FastAPI(title="ReStock AI", version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def _no_store(request, call_next):
    """Prevent browsers caching the dashboard assets or API responses, so a
    fresh deploy (or a mid-demo edit) is always what the viewer sees."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_url(request: Optional[Request]) -> str:
    """Absolute base for the Approve/Reject deep links. A configured
    PUBLIC_BASE_URL wins (correct behind the Alternate Clouds proxy); otherwise
    derive it from the incoming request."""
    if settings.public_base_url:
        return settings.public_base_url
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


async def _process(sit: Situation, *, execute: bool = True,
                   request: Optional[Request] = None) -> Decision:
    """Encode -> decide -> gate -> (execute / escalate / block). SRS Section 3."""
    remaining = store.budget
    state = agent.encode_state(
        sit.inventory_level, sit.demand_forecast, sit.unit_price, remaining
    )
    action, confidence, visits, detail = agent.decide(state)
    quantity = agent.quantity_for(action, sit.demand_forecast)
    cost = round(quantity * sit.unit_price, 2)

    gate = evaluate(action, quantity, cost, confidence, visits, remaining,
                    settings, conf_threshold=store.effective_conf_threshold)

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
        confidence_margin=float(detail.get("margin", 0.0)),
        confidence_familiarity=float(detail.get("familiarity", 0.0)),
        route=gate.route,
        reasons=gate.reasons,
        status="PENDING",
        budget_after=remaining,
    )

    store.note_decision(sit.product_id, action, gate.route)

    if gate.route == ROUTE_BLOCK:
        decision.status = "BLOCKED"
    elif gate.route == ROUTE_AUTO:
        decision.status = "AUTO_APPROVED"
        if execute:
            store.spend(cost)
            store.add_stock(sit.product_id, quantity)  # replenish stock
            if cost > 0:
                po, fulfillment = await fulfill_order(decision, settings)
                decision.po_number = po
                decision.fulfillment = fulfillment
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
        if settings.discord_enabled:
            decision.notified = await notify_escalation(
                decision, settings, base_url=_base_url(request))

    store.add(decision)
    return decision


# ---- API (SRS Section 9) -------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    # Must be fast and independent of the model/LLM so platform checks pass.
    return {"status": "ok"}


@app.post("/api/run-batch", response_model=List[Decision])
async def run_batch(request: Request) -> List[Decision]:
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
        results.append(await _process(sit, request=request))
    return results


@app.post("/api/evaluate", response_model=Decision)
async def evaluate_one(sit: Situation, request: Request) -> Decision:
    if not agent.loaded:
        raise HTTPException(503, "Model not loaded")
    return await _process(sit, request=request)


@app.get("/api/decisions", response_model=List[Decision])
def decisions(status: str | None = None) -> List[Decision]:
    return store.all(status)


@app.get("/api/pending", response_model=List[Decision])
def pending() -> List[Decision]:
    return store.pending()


async def _approve(decision_id: str) -> Decision:
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
    if d.cost > 0:
        d.po_number, d.fulfillment = await fulfill_order(d, settings)
    store.record_feedback(agreed=True)   # human agreed with the AI
    store.persist(d)
    return d


async def _reject(decision_id: str) -> Decision:
    d = store.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    if d.status != "PENDING":
        raise HTTPException(409, f"Decision already resolved ({d.status})")
    d.status = "HUMAN_REJECTED"
    d.human_decision = "REJECTED"
    store.record_feedback(agreed=False)  # human overrode the AI
    store.persist(d)
    return d


@app.post("/api/decisions/{decision_id}/approve", response_model=Decision)
async def approve(decision_id: str) -> Decision:
    return await _approve(decision_id)


@app.post("/api/decisions/{decision_id}/reject", response_model=Decision)
async def reject(decision_id: str) -> Decision:
    return await _reject(decision_id)


@app.get("/api/metrics", response_model=Metrics)
def metrics() -> Metrics:
    return store.metrics()


@app.get("/api/decisions.csv")
def decisions_csv() -> Response:
    """Download the full audit log as CSV (SRS 7 — auditability)."""
    cols = [
        "timestamp", "product_id", "product_name", "action", "quantity",
        "unit_price", "cost", "confidence", "route", "status", "reasons",
        "po_number", "fulfillment", "rationale_source", "budget_after",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for d in store.all():
        w.writerow([
            d.timestamp, d.product_id, d.product_name, d.action, d.quantity,
            d.unit_price, d.cost, d.confidence, d.route, d.status,
            "|".join(d.reasons), d.po_number or "", d.fulfillment,
            d.rationale_source, d.budget_after,
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=restock_audit_log.csv"},
    )


# ---- demo scenario presets ----------------------------------------------
PRESETS = [
    {"key": "routine", "label": "Routine restock",
     "hint": "Low-risk order the AI should auto-execute",
     "situation": {"product_name": "Table Salt 1kg", "inventory_level": 40,
                   "demand_forecast": 100, "unit_price": 3.0}},
    {"key": "expensive", "label": "Expensive order",
     "hint": "Over the auto-approval limit → human sign-off",
     "situation": {"product_name": "Imported Arabica Coffee", "inventory_level": 20,
                   "demand_forecast": 300, "unit_price": 26.0}},
    {"key": "unfamiliar", "label": "Unfamiliar situation",
     "hint": "A rarely-seen state → escalate",
     "situation": {"product_name": "Seasonal Mango Pulp", "inventory_level": 30,
                   "demand_forecast": 150, "unit_price": 19.0}},
    {"key": "blocked", "label": "Over the hard ceiling",
     "hint": "Too big / too costly → blocked outright",
     "situation": {"product_name": "Bulk Diesel Generator", "inventory_level": 5,
                   "demand_forecast": 260, "unit_price": 55.0}},
]


@app.get("/api/presets")
def presets() -> List[dict]:
    return PRESETS


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
async def reorder(product_id: str, request: Request) -> Decision:
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
    return await _process(sit, request=request)


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
        "integrations": {
            "persistence": bool(store.db and store.db.ok),
            "discord_notifications": settings.discord_enabled,
            "order_fulfillment_webhook": settings.fulfillment_enabled,
            "public_base_url": settings.public_base_url or None,
        },
        "constants": {
            "auto_limit": settings.auto_limit,
            "hard_limit": settings.hard_limit,
            "max_order_qty": settings.max_order_qty,
            "conf_threshold": settings.conf_threshold,
            "effective_conf_threshold": store.effective_conf_threshold,
            "min_visits_familiar": settings.min_visits_familiar,
        },
    }


# ---- one-click Approve / Reject (from a Discord/email link) --------------
def _result_page(title: str, body: str, ok: bool) -> HTMLResponse:
    color = "#2ecc71" if ok else "#e74c3c"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} · ReStock AI</title>
<style>
  body{{margin:0;font-family:system-ui,Segoe UI,Inter,sans-serif;background:#0b1020;color:#e6ebff;
       display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}}
  .card{{background:#121a33;border:1px solid #243156;border-radius:16px;padding:32px 28px;max-width:460px;
         box-shadow:0 20px 60px rgba(0,0,0,.4);text-align:center}}
  .mark{{width:56px;height:56px;border-radius:50%;background:{color};color:#06101f;font-size:30px;
         display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-weight:800}}
  h1{{font-size:20px;margin:0 0 8px}} p{{color:#9fb0d9;line-height:1.5;margin:6px 0}}
  a{{display:inline-block;margin-top:18px;color:#7aa2ff;text-decoration:none;font-weight:600}}
</style></head><body><div class="card">
  <div class="mark">{'✓' if ok else '✕'}</div>
  <h1>{title}</h1>{body}
  <a href="/">← Open the dashboard</a>
</div></body></html>"""
    return HTMLResponse(html)


def _decision_summary_html(d: Decision) -> str:
    return (
        f"<p><strong>{d.product_name}</strong></p>"
        f"<p>{d.action.replace('_', ' ').title()} · {d.quantity} units · ${d.cost:,.0f}</p>"
    )


@app.get("/a/{decision_id}")
async def approve_link(decision_id: str) -> HTMLResponse:
    try:
        d = await _approve(decision_id)
    except HTTPException as exc:
        return _result_page("Could not approve", f"<p>{exc.detail}</p>", ok=False)
    po = f"<p>Purchase order <strong>{d.po_number}</strong> issued.</p>" if d.po_number else ""
    return _result_page("Order approved", _decision_summary_html(d) + po, ok=True)


@app.get("/r/{decision_id}")
async def reject_link(decision_id: str) -> HTMLResponse:
    try:
        d = await _reject(decision_id)
    except HTTPException as exc:
        return _result_page("Could not reject", f"<p>{exc.detail}</p>", ok=False)
    return _result_page("Order rejected", _decision_summary_html(d) +
                        "<p>No purchase order was issued.</p>", ok=False)


# ---- static dashboard ----------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
