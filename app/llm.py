"""LLM explanation layer (SRS Section 8).

Turns a structured escalation into one short, plain-English paragraph. This is
the ONLY place an LLM is used; the purchasing decision is made by the Q-table,
not the LLM. If no key is set or the call fails/times out, a deterministic
templated rationale is used instead. This function never raises to the caller.
"""
from __future__ import annotations

from typing import Dict, Tuple

import httpx

from .config import Settings
from .gate import REASON_LABELS

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4o-mini",
}

LEVEL_WORDS = {"LOW": "low", "MEDIUM": "moderate", "HIGH": "high"}


def _reasons_phrase(reasons) -> str:
    labels = [REASON_LABELS.get(r, r).lower() for r in reasons]
    if not labels:
        return "a policy check"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def fallback_rationale(ctx: Dict) -> str:
    """Deterministic template (SRS 8.3). Always available."""
    inv, fc, _price, _bud = ctx["state"]
    return (
        f"Recommended {ctx['action'].replace('_', ' ').lower()} of "
        f"{ctx['quantity']} units of {ctx['product_name']} "
        f"(inventory {LEVEL_WORDS.get(inv, inv.lower())}, forecast demand "
        f"{LEVEL_WORDS.get(fc, fc.lower())}, estimated cost ${ctx['cost']:,.0f}). "
        f"Escalated for your sign-off because of {_reasons_phrase(ctx['reasons'])}."
    )


def _build_prompt(ctx: Dict) -> str:
    return (
        "You are assisting an operations manager who approves purchase orders. "
        "Write 2-3 sentences of plain business English (no jargon, no bullet "
        "points) explaining this recommended order and why a human is being "
        "asked to approve it. State what is recommended, the key situational "
        "facts, and the reason for escalation.\n\n"
        f"Product: {ctx['product_name']}\n"
        f"Recommended action: {ctx['action']} ({ctx['quantity']} units)\n"
        f"Unit price: ${ctx['unit_price']:,.2f}\n"
        f"Estimated cost: ${ctx['cost']:,.2f}\n"
        f"Remaining budget: ${ctx['remaining_budget']:,.2f}\n"
        f"State (inventory, forecast, price, budget): {', '.join(ctx['state'])}\n"
        f"Confidence: {ctx['confidence']:.0%}\n"
        f"Escalation reasons: {_reasons_phrase(ctx['reasons'])}\n"
    )


async def _call_anthropic(ctx: Dict, settings: Settings) -> str:
    model = settings.llm_model or DEFAULT_MODELS["anthropic"]
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 160,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": _build_prompt(ctx)}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(
            block.get("text", "") for block in data.get("content", [])
        ).strip()


async def _call_openai(ctx: Dict, settings: Settings) -> str:
    model = settings.llm_model or DEFAULT_MODELS["openai"]
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 160,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": _build_prompt(ctx)}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def generate_rationale(ctx: Dict, settings: Settings) -> Tuple[str, str]:
    """Return (text, source) where source is "llm" or "fallback"."""
    if not settings.llm_enabled:
        return fallback_rationale(ctx), "fallback"
    try:
        if settings.llm_provider == "openai":
            text = await _call_openai(ctx, settings)
        else:
            text = await _call_anthropic(ctx, settings)
        if text:
            return text, "llm"
    except Exception:  # noqa: BLE001 - any failure degrades to the template
        pass
    return fallback_rationale(ctx), "fallback"
