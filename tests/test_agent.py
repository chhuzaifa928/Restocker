"""Agent inference, state encoding and confidence (SRS 6.3, 7.2)."""
from app.agent import Agent
from app.config import settings


def _agent() -> Agent:
    a = Agent(settings)
    a.load()
    return a


def test_model_loads():
    a = _agent()
    assert a.loaded and a.load_error is None and a.q_table


def test_state_encoding_levels():
    a = _agent()
    # Far below median inventory -> LOW; far above forecast -> HIGH.
    state = a.encode_state(1.0, 10_000.0, a.median_price, a.monthly_budget)
    assert state[0] == "LOW" and state[1] == "HIGH" and len(state) == 4


def test_decide_returns_breakdown():
    a = _agent()
    state = a.encode_state(10, 120, 10, a.monthly_budget)
    action, conf, visits, detail = a.decide(state)
    assert action in a.actions
    assert 0.0 <= conf <= 1.0
    assert {"margin", "familiarity", "q_values"} <= set(detail)
    # Confidence is the documented blend of margin and familiarity.
    expected = 0.7 * detail["margin"] + 0.3 * detail["familiarity"]
    assert abs(conf - round(expected, 4)) < 1e-6


def test_unseen_state_waits():
    a = _agent()
    action, conf, visits, _ = a.decide(["LOW", "LOW", "LOW", "ZZZ"])
    assert action == "WAIT" and conf == 0.0 and visits == 0


def test_quantity_scales_with_forecast():
    a = _agent()
    assert a.quantity_for("WAIT", 100) == 0
    assert a.quantity_for("BUY_MEDIUM", 100) == 100
    assert a.quantity_for("BUY_LARGE", 100) == 200
