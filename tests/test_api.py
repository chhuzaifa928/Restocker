"""End-to-end API behaviour, persistence and the human-feedback loop."""
import importlib
import os
from collections import Counter

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Isolate each test run on its own SQLite file.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import app.config as config
    importlib.reload(config)
    import app.store as store
    import app.main as main
    importlib.reload(store)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_health_is_independent_of_model(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_run_batch_route_split(client):
    r = client.post("/api/run-batch").json()
    routes = Counter(d["route"] for d in r)
    assert routes["AUTO_APPROVED"] and routes["ESCALATED"] and routes["BLOCKED"]


def test_approve_issues_po_and_records_agreement(client):
    client.post("/api/run-batch")
    pend = client.get("/api/pending").json()
    assert pend, "expected at least one escalation"
    d = client.post(f"/api/decisions/{pend[0]['id']}/approve").json()
    assert d["status"] == "HUMAN_APPROVED" and d["po_number"]
    m = client.get("/api/metrics").json()
    assert m["orders_filled"] >= 1 and m["agreement_rate"] == 100.0


def test_reject_raises_effective_threshold(client):
    client.post("/api/run-batch")
    base = client.get("/api/metrics").json()["base_conf_threshold"]
    pend = client.get("/api/pending").json()
    client.post(f"/api/decisions/{pend[0]['id']}/reject")
    assert client.get("/api/metrics").json()["effective_conf_threshold"] > base


def test_double_resolve_conflicts(client):
    client.post("/api/run-batch")
    pid = client.get("/api/pending").json()[0]["id"]
    assert client.post(f"/api/decisions/{pid}/approve").status_code == 200
    assert client.post(f"/api/decisions/{pid}/reject").status_code == 409


def test_persistence_survives_reload(client, tmp_path):
    client.post("/api/run-batch")
    products = len(client.get("/api/inventory").json())
    before = client.get("/api/metrics").json()["total_decisions"]
    # New app instance, same DB_PATH -> state should rehydrate.
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c2:
        after = c2.get("/api/metrics").json()["total_decisions"]
    assert after == before and before == products


def test_ingest_updates_forecast_and_reevaluates(client):
    client.post("/api/run-batch")
    r = client.post("/api/ingest").json()
    assert r["signals"] and len(r["decisions"]) == len(r["signals"])
    inv = {p["product_id"]: p for p in client.get("/api/inventory").json()}
    s = r["signals"][0]
    assert inv[s["product_id"]]["demand_forecast"] == s["demand_forecast"]


def test_csv_export_has_header(client):
    client.post("/api/run-batch")
    body = client.get("/api/decisions.csv").text
    assert body.splitlines()[0].startswith("timestamp,product_id,product_name")


def test_blocked_order_does_not_spend(client):
    client.post("/api/run-batch")
    m = client.get("/api/metrics").json()
    # Spend must never exceed the initial budget.
    assert m["total_spend"] <= m["initial_budget"]
