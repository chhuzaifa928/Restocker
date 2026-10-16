"""SQLite persistence (SRS 5.2 — upgraded from pure in-memory).

The in-memory :class:`~app.store.Store` stays the fast source of truth; this
module mirrors every mutation to a small SQLite file so decisions, the budget
pool, live inventory and the human-feedback counters survive a container
restart (which Alternate Clouds does on redeploy). If the DB cannot be opened
the app degrades gracefully to memory-only (NFR-1) and never crashes.

Three tables, all tiny:
  * decisions(id, data)   — one row per decision, the full record as JSON
  * inventory(pid, ord, data) — live stock per product, ``ord`` preserves order
  * meta(key, value)      — budget, units_replenished, feedback, threshold …
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.ok = False
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inventory (pid TEXT PRIMARY KEY, ord INTEGER, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            self._conn.commit()
            self.ok = True
        except Exception as exc:  # noqa: BLE001 - degrade to memory-only
            self.error = f"{type(exc).__name__}: {exc}"
            self._conn = None

    # ---- internal --------------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> None:
        if not self._conn:
            return
        try:
            with self._lock:
                self._conn.execute(sql, params)
                self._conn.commit()
        except Exception:  # noqa: BLE001 - never let persistence break a request
            pass

    def _many(self, sql: str, rows: List[tuple]) -> None:
        if not self._conn or not rows:
            return
        try:
            with self._lock:
                self._conn.executemany(sql, rows)
                self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    # ---- decisions -------------------------------------------------------
    def save_decision(self, decision_id: str, data: Dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO decisions (id, data) VALUES (?, ?)",
            (decision_id, json.dumps(data)),
        )

    def clear_decisions(self) -> None:
        self._exec("DELETE FROM decisions")

    def load_decisions(self) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        try:
            with self._lock:
                cur = self._conn.execute("SELECT data FROM decisions")
                return [json.loads(r[0]) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            return []

    # ---- inventory -------------------------------------------------------
    def save_inventory(self, order: List[str], inv: Dict[str, dict]) -> None:
        if not self._conn:
            return
        rows = [(pid, i, json.dumps(inv[pid])) for i, pid in enumerate(order) if pid in inv]
        self._exec("DELETE FROM inventory")
        self._many("INSERT OR REPLACE INTO inventory (pid, ord, data) VALUES (?, ?, ?)", rows)

    def save_product(self, pid: str, order_index: int, data: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO inventory (pid, ord, data) VALUES (?, ?, ?)",
            (pid, order_index, json.dumps(data)),
        )

    def load_inventory(self) -> Tuple[Optional[List[str]], Optional[Dict[str, dict]]]:
        if not self._conn:
            return None, None
        try:
            with self._lock:
                cur = self._conn.execute("SELECT pid, data FROM inventory ORDER BY ord ASC")
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001
            return None, None
        if not rows:
            return None, None
        order = [r[0] for r in rows]
        inv = {r[0]: json.loads(r[1]) for r in rows}
        return order, inv

    # ---- meta ------------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        if not self._conn:
            return default
        try:
            with self._lock:
                cur = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
                row = cur.fetchone()
        except Exception:  # noqa: BLE001
            return default
        return json.loads(row[0]) if row else default

    def clear_meta(self) -> None:
        self._exec("DELETE FROM meta")
