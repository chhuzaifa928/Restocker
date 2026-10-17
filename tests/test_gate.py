"""Dual-gate routing (SRS Section 6)."""
from app.config import settings
from app.gate import ROUTE_AUTO, ROUTE_BLOCK, ROUTE_ESCALATE, evaluate


def test_block_over_qty_cap():
    r = evaluate("BUY_LARGE", settings.max_order_qty + 1, 100.0, 0.9, 10, 1e9, settings)
    assert r.route == ROUTE_BLOCK and "qty_over_limit" in r.reasons


def test_block_over_hard_limit():
    r = evaluate("BUY_LARGE", 10, settings.hard_limit + 1, 0.9, 10, 1e9, settings)
    assert r.route == ROUTE_BLOCK and "cost_over_hard_limit" in r.reasons


def test_block_over_budget():
    r = evaluate("BUY_SMALL", 10, 500.0, 0.9, 10, 100.0, settings)
    assert r.route == ROUTE_BLOCK and "over_budget" in r.reasons


def test_wait_always_auto_approves_even_when_unfamiliar():
    r = evaluate("WAIT", 0, 0.0, 0.0, 0, 1e9, settings)
    assert r.route == ROUTE_AUTO and r.reasons == []


def test_escalate_over_auto_limit():
    r = evaluate("BUY_LARGE", 10, settings.auto_limit + 1, 0.99, 50, 1e9, settings)
    assert r.route == ROUTE_ESCALATE and "cost_over_limit" in r.reasons


def test_escalate_low_confidence():
    r = evaluate("BUY_SMALL", 10, 100.0, 0.2, 50, 1e9, settings)
    assert r.route == ROUTE_ESCALATE and "low_confidence" in r.reasons


def test_escalate_unfamiliar_state():
    r = evaluate("BUY_SMALL", 10, 100.0, 0.99, 0, 1e9, settings,
                 conf_threshold=settings.conf_threshold)
    assert r.route == ROUTE_ESCALATE and "unfamiliar_state" in r.reasons


def test_routine_auto_approves():
    r = evaluate("BUY_SMALL", 10, 100.0, 0.99, 50, 1e9, settings)
    assert r.route == ROUTE_AUTO and r.reasons == []


def test_block_precedence_over_escalate():
    # Both over-budget (block) and low-confidence (escalate) -> BLOCK wins.
    r = evaluate("BUY_LARGE", 10, 1e6, 0.1, 0, 100.0, settings)
    assert r.route == ROUTE_BLOCK


def test_effective_threshold_override_escalates():
    # Confidence that passes the base bar but fails a raised effective bar.
    r = evaluate("BUY_SMALL", 10, 100.0, 0.65, 50, 1e9, settings, conf_threshold=0.80)
    assert r.route == ROUTE_ESCALATE and "low_confidence" in r.reasons
