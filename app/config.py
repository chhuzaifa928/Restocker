"""Runtime configuration.

All tunables come from environment variables (with safe defaults) per SRS
Section 6 / 12.2. No secrets are hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load a local .env if present (git-ignored). On the cloud, real env vars win.
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = BASE_DIR / "model"
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class Settings:
    # Dual-gate constants (SRS Section 6).
    auto_limit: float = _f("AUTO_LIMIT", 5000.0)
    hard_limit: float = _f("HARD_LIMIT", 12000.0)
    max_order_qty: int = _i("MAX_ORDER_QTY", 400)
    conf_threshold: float = _f("CONF_THRESHOLD", 0.60)
    min_visits_familiar: int = _i("MIN_VISITS_FAMILIAR", 5)

    # LLM explanation layer (SRS Section 8).
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_model: str = os.getenv("LLM_MODEL", "").strip()
    llm_timeout: float = _f("LLM_TIMEOUT", 5.0)

    # Paths.
    q_table_path: Path = MODEL_DIR / "q_table.json"
    config_path: Path = MODEL_DIR / "config.json"
    seed_path: Path = DATA_DIR / "inventory_seed.csv"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
