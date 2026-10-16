"""ReStock AI — MCP server (Model Context Protocol).

Exposes the running ReStock service as MCP tools, so any MCP client (Claude
Desktop, an agent framework, etc.) can drive the human-governed purchasing
officer directly:

    evaluate_reorder   — ask the Q-table agent what to do for a situation
    list_pending       — see escalations awaiting human sign-off
    approve_order      — a human approves an escalated order (issues a PO)
    reject_order       — a human rejects (overrides the AI)
    get_metrics        — dashboard summary (auto %, budget, feedback, …)
    get_inventory      — live per-product stock positions

This talks to the FastAPI service over HTTP (RESTOCK_API_URL, default
http://127.0.0.1:8000), so an approval here affects the *same* live system the
dashboard and Discord links do — one coherent agent, many front doors.

Run it (separate from the web service — see requirements-mcp.txt):
    pip install -r requirements-mcp.txt
    RESTOCK_API_URL=https://your-app.on-alternate-clouds python mcp_server.py
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

# The official SDK renamed FastMCP -> MCPServer in v2; support both lines.
try:
    from mcp.server.mcpserver import MCPServer as _Server   # mcp >= 2.x
except ModuleNotFoundError:
    try:
        from mcp.server.fastmcp import FastMCP as _Server    # mcp 1.x
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit(
            "The MCP SDK is not installed. Run:  pip install -r requirements-mcp.txt"
        ) from exc

API = os.getenv("RESTOCK_API_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = float(os.getenv("RESTOCK_API_TIMEOUT", "15"))

mcp = _Server("ReStock AI")


def _get(path: str, **params):
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.get(f"{API}{path}", params=params or None)
        r.raise_for_status()
        return r.json()


def _post(path: str, json: Optional[dict] = None):
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(f"{API}{path}", json=json)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def evaluate_reorder(product_name: str, inventory_level: float,
                     demand_forecast: float, unit_price: float) -> dict:
    """Evaluate one purchasing situation. The pre-trained Q-table agent picks an
    action and the dual gate routes it (auto-execute / escalate / block).
    Returns the full decision record including the AI rationale when escalated."""
    return _post("/api/evaluate", {
        "product_id": f"MCP-{abs(hash((product_name, inventory_level)))%100000}",
        "product_name": product_name,
        "inventory_level": inventory_level,
        "demand_forecast": demand_forecast,
        "unit_price": unit_price,
    })


@mcp.tool()
def list_pending() -> list:
    """List escalated orders currently awaiting a human decision."""
    return _get("/api/pending")


@mcp.tool()
def approve_order(decision_id: str) -> dict:
    """Approve an escalated order as the human-in-the-loop. Executes the order,
    issues a purchase order, and records agreement with the AI."""
    return _post(f"/api/decisions/{decision_id}/approve")


@mcp.tool()
def reject_order(decision_id: str) -> dict:
    """Reject an escalated order (override the AI). No purchase order is issued;
    the override is recorded and makes the gate more cautious."""
    return _post(f"/api/decisions/{decision_id}/reject")


@mcp.tool()
def get_metrics() -> dict:
    """Dashboard summary: auto-approval %, escalations, budget, units
    replenished, human agreement/override rates and the adaptive threshold."""
    return _get("/api/metrics")


@mcp.tool()
def get_inventory() -> list:
    """Live per-product stock positions with LOW/HEALTHY/HIGH status."""
    return _get("/api/inventory")


@mcp.tool()
def run_batch() -> dict:
    """Run the agent over the whole seed dataset (resets state first). Returns a
    count of each route taken."""
    decisions = _post("/api/run-batch")
    routes: dict = {}
    for d in decisions:
        routes[d["route"]] = routes.get(d["route"], 0) + 1
    return {"decisions": len(decisions), "routes": routes}


if __name__ == "__main__":
    mcp.run()
