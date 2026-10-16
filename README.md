# ReStock AI — A Human-Governed Autonomous Purchasing Officer

An AI-driven stock **replenishment** module for a distributor. A pre-trained
**Q-Learning** agent decides how much to reorder for each product, **auto-executes
the routine low-risk orders**, and **escalates the expensive / uncertain / unfamiliar
orders to a human** for one-click approval. An LLM writes the plain-English rationale
for each escalation — it *explains*, it does not *decide*.

> Theme — *Human + AI: Better Together*: the AI handles the volume, the human owns the judgment.

Built from `ReStock_AI_SRS.md` (SRS v1.0).

### New in v2.0 — the agent does real work, across real tools
- **Discord approval notifications.** Every escalation is pushed to a Discord
  (or Slack-compatible) webhook with the AI's rationale and **one-click
  Approve / Reject links** — a manager can sign off a purchase order from their
  phone, not just the dashboard.
- **Real order fulfillment.** Executed orders (auto-approved or human-approved)
  raise a **purchase order** (PO number + receipt) and optionally POST it to a
  downstream webhook (supplier intake, a Google Sheet, n8n/Zapier …).
- **SQLite persistence.** Decisions, budget and feedback survive a redeploy.
- **Human-feedback loop (adaptive gate).** Approvals/rejections are recorded as
  agreement/override; a run of rejections *raises* the confidence bar so the
  gate escalates more cautiously. Governance only — the Q-table is never retrained.
- **MCP server.** `mcp_server.py` exposes the live service as Model Context
  Protocol tools, so any MCP client (e.g. Claude Desktop) can drive it.
- **Demo polish.** Scenario preset buttons, a one-click CSV audit-log export,
  per-decision confidence breakdown (margin vs familiarity) and live PO numbers.

### Dashboard
Two tabbed views with live polling, animated counters, and toast notifications:
- **Decisions** — the audit-log feed + the human approval queue (the hero panel).
- **Inventory** — live stock cards per product with a stock bar, coverage, LOW/HEALTHY/
  HIGH status (low-stock cards pulse), and **working controls**: *Evaluate reorder*
  (runs the agent on that product at its current stock) and a ± stepper for manual
  stock corrections. Executed orders **replenish stock** in real time.

---

## Quick start (local)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000** and click **Run batch**.

No API key is required to run — without one, the app uses a deterministic
templated rationale. To enable live LLM explanations, copy `.env.example` to
`.env` and set `LLM_API_KEY` (and `LLM_PROVIDER`).

---

## How it works (pipeline)

```
situation → encode state (LOW/MED/HIGH ×4) → Q-table argmax (ε=0)
          → quantity + cost + confidence → DUAL GATE
          → AUTO-EXECUTE  |  ESCALATE (LLM rationale → human)  |  BLOCK
          → dashboard + audit log
```

### The dual gate (first match wins)
1. **BLOCK** — `qty > MAX_ORDER_QTY`, `cost > HARD_LIMIT`, or `cost > remaining budget`
2. **WAIT** actions always auto-approve (safe no-op)
3. **ESCALATE** — `cost > AUTO_LIMIT` · `confidence < CONF_THRESHOLD` · `visits < MIN_VISITS_FAMILIAR`
   (all applicable reasons are recorded)
4. otherwise **AUTO-APPROVE**

### Confidence (SRS 6.3)
`confidence = 0.7 · margin + 0.3 · familiarity`, where *margin* is how decisively the
agent preferred its action and *familiarity* is how well-trained that state is.

---

## API (prefix `/api`)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/health` | Liveness check (independent of model/LLM) |
| POST | `/api/run-batch` | Run the agent over the seed dataset (resets state first) |
| POST | `/api/evaluate` | Evaluate one manually-entered situation |
| GET  | `/api/decisions` | All decision records (`?status=` filter) |
| GET  | `/api/pending` | Only pending escalations |
| POST | `/api/decisions/{id}/approve` | Human approves an escalated order |
| POST | `/api/decisions/{id}/reject` | Human rejects an escalated order |
| GET  | `/api/metrics` | Dashboard summary metrics (incl. low-stock, units replenished) |
| GET  | `/api/inventory` | Live per-product stock positions + status |
| POST | `/api/inventory/{id}/reorder` | Evaluate one product at its current stock |
| POST | `/api/inventory/{id}/adjust` | Manual stock correction (`{delta}` or `{level}`) |
| GET  | `/api/model-status` | Model/LLM/integrations/constants (diagnostics) |
| GET  | `/api/decisions.csv` | Download the full audit log as CSV |
| GET  | `/api/presets` | Demo scenario presets (auto / escalate / block) |
| GET  | `/a/{id}` · `/r/{id}` | One-click Approve / Reject (used by Discord links) |

---

## Project layout

```
app/        FastAPI service, agent, dual gate, LLM layer, store, schemas
  db.py       SQLite persistence (state survives a redeploy)
  fulfill.py  purchase-order fulfillment (PO number + optional webhook)
  notify.py   Discord/Slack escalation notifications + approve/reject links
model/      q_table.json + config.json  (pre-trained artifacts; inference only)
data/       inventory_seed.csv          (demo situations: all three routes)
static/     index.html, app.js, styles.css  (single-page dashboard)
mcp_server.py      MCP server (separate process; see requirements-mcp.txt)
requirements-mcp.txt   MCP extras — kept out of the web deploy on purpose
```

---

## Integrations (all optional — the app runs fully without any of them)

Set these as environment variables on Alternate Clouds (never commit real
values). See `.env.example` for the full list.

| Variable | Effect |
|---|---|
| `DB_PATH` | SQLite file for persistence (empty = pure in-memory MVP mode) |
| `NOTIFY_DISCORD_WEBHOOK_URL` | Push escalations to Discord with Approve/Reject links |
| `PURCHASE_ORDER_WEBHOOK_URL` | POST a PO receipt downstream on every executed order |
| `PUBLIC_BASE_URL` | Your deployed URL — makes the Discord Approve/Reject links absolute |

Every integration degrades gracefully: a missing or failing webhook never
breaks a decision, voids an order, or crashes the service.

### MCP server (Model Context Protocol)

`mcp_server.py` exposes the live service as MCP tools (`evaluate_reorder`,
`list_pending`, `approve_order`, `reject_order`, `get_metrics`,
`get_inventory`, `run_batch`). It talks to the running API over HTTP, so an
approval made through MCP affects the *same* system as the dashboard and the
Discord links. It is deliberately **not** part of `requirements.txt` so it can
never jeopardise the mandatory web deployment.

```bash
pip install -r requirements-mcp.txt
RESTOCK_API_URL=https://your-app.on-alternate-clouds python mcp_server.py
```

---

## Notes on the model artifacts

This build has **no training notebook**, so `model/q_table.json` and
`model/config.json` were **synthesised** with an explainable heuristic policy
(see `scripts`/generation in the project history) that covers all 81 discretised
states and is deterministic. The application performs **inference only** and never
modifies the Q-table. Swap in a notebook-exported Q-table with the same schema
(`meta.actions`, `states[key][action]`, optional `_visits`) and the app works
unchanged.

### Design decisions resolved from the SRS
- **Budget is a single global pool** initialised from `config.monthly_budget` and
  decremented as orders execute (the SRS dashboard shows one "Budget remaining").
  The CSV `remaining_budget` column carries the starting budget.
- **`WAIT` always auto-approves** even in an unfamiliar state — an explicit
  short-circuit in the gate, resolving an ambiguity in SRS §6.1 vs §6.3.
- **`run-batch` is idempotent** — it resets decisions and budget each run, so the
  presenter can click it repeatedly during the demo without double-spending.

---

## Deployment

One repo, one service. A `Dockerfile` is included; platforms that inject `$PORT`
are handled. Set environment variables (`LLM_API_KEY`, gate constants) in the
platform dashboard — never commit `.env`.

Verify after deploy: the public URL serves the dashboard and `/api/health`
returns `{"status":"ok"}`.
