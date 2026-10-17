# 🎤 ReStock AI — 6-Minute Finalist Demo Script

*Agentika 2.0 · theme: Human + AI: Better Together. Finalists get **6 minutes**.
Every beat below names the rubric criterion it scores.*

## ✅ Pre-demo checklist (do this BEFORE you present)
- [ ] App is **deployed live on Alternate Clouds** and the public URL opens (mandatory — local-only disqualifies).
- [ ] Env vars set on the platform: `PUBLIC_BASE_URL` (your URL), `NOTIFY_DISCORD_WEBHOOK_URL`, and `LLM_API_KEY` for live rationales.
- [ ] **Three browser tabs open & logged in:** (1) the dashboard, (2) the Discord channel receiving escalations, (3) the live URL on your phone (to approve from Discord on mobile — big "wow").
- [ ] Click **Run batch** once beforehand so the LLM is warm, then **Run batch** again live.
- [ ] Toggle to the theme that reads best on the projector (usually **dark**).

---

## ⏱️ The script

### 0:00 – 0:30 · Hook *(Usefulness)*
> "Every distributor faces the same problem: thousands of restock decisions a week. Automate all of them and you get expensive mistakes. Do them by hand and you drown. **ReStock AI** gives the AI the routine volume and keeps the human in charge of the risky calls."

### 0:30 – 1:15 · The idea + theme *(Theme alignment)*
> "A pre-trained Q-learning agent decides how much to reorder. A safety gate then does one of three things: **auto-execute** the routine orders, **escalate** the expensive or uncertain ones to a human, or **block** the unsafe ones. The AI handles volume; the human owns judgment — that's *Human + AI: Better Together*."
*(Show the architecture diagram slide or the dashboard header.)*

### 1:15 – 2:30 · Live decision run *(Technical execution + UX)*
1. Click **Run batch**.
2. Point to the metrics animating: *"Across 30 products it auto-approved 21, escalated 6, and blocked 3 — the agent cleared the routine volume in one click."*
3. Point to a **BLOCKED** row: *"This generator order is over our hard ceiling — the AI refused to spend it."*
4. Point to the **confidence bars** and an escalation's **rationale**: *"The LLM explains each escalation in plain English — but it never makes the decision; the Q-table does."*

### 2:30 – 3:45 · The human-in-the-loop (the hero moment) *(Theme + Automation depth)*
1. Switch to **Discord** — the escalation is already there with Approve/Reject buttons.
2. On your **phone**, tap **✅ Approve**.
3. Switch back to the dashboard — the card clears, stock replenishes, and a **PO number** appears: *"Approved from Discord on my phone — and the agent filed a real purchase order, `PO-…`. That's the AI doing actual work across tools, not just drawing a chart."*
4. **Reject** a different one on the dashboard: *"When I override the AI, the **adaptive gate** raises the confidence bar — watch the 'Adaptive gate' readout tick up. It learns to be more cautious, without ever retraining the model."*

### 3:45 – 4:45 · Reactive ingestion *(Creativity + Automation depth)*
1. Click **📡 Ingest demand**.
2. *"A demand signal just arrived — a spike on coffee, a dip elsewhere. The agent re-evaluated those products **on its own** and routed each one. New demand in, decisions out — a closed loop, no human needed for the routine part."*

### 4:45 – 5:20 · Depth & honesty *(Creativity + Technical)*
> "Three more things under the hood: it's driveable as an **MCP server** — the same agent from Claude Desktop. State is **persisted**, so a redeploy doesn't lose the budget or the audit log — which you can **export to CSV**. And to be straight with you: the Q-table here is a synthesised, *explainable* policy, not a trained one — but it's a drop-in artifact, so a real trained table plugs straight in."

### 5:20 – 6:00 · Close + ask *(Usefulness)*
> "So: AI handles the volume, the human owns the judgment, and the system does real work — purchase orders, Discord approvals, a reactive demand loop — all deployed live on Alternate Clouds. That's ReStock AI. Thank you — happy to take questions."

---

## 🛡️ Q&A prep — likely judge questions
- **"Is the model actually trained?"** → "No — it's a synthesised, explainable policy covering all 81 states, and it's a pluggable artifact: export a trained Q-table with the same schema and it drops in unchanged. We chose explainability and a reliable demo over a black box."
- **"What's genuinely novel vs a chatbot wrapper?"** → "The LLM never decides — the gate and Q-table do. The novelty is the *governance*: a dual gate, human escalation across Discord, and a feedback loop that adapts the gate from human overrides."
- **"What happens if the LLM/webhook is down?"** → "Everything degrades gracefully — a deterministic rationale fallback, and a failed webhook never voids an order or crashes the service."
- **"Is it really deployed?"** → *(Open the public URL + `/api/health`.)*
- **"How would this scale?"** → "Swap the in-memory+SQLite store for Postgres and the webhook target for a real supplier/ERP endpoint — the gate and agent are unchanged."

## 📊 Rubric coverage map
| Rubric criterion | Where it's demonstrated |
|---|---|
| Technical execution & reliability (25) | Run batch, graceful degradation, persistence, `pytest` suite |
| Usefulness & real-world impact (20) | Opening hook, PO fulfillment, budget guard |
| Theme alignment (15) | Escalate-to-human, Discord approval |
| Creativity & uniqueness (15) | Adaptive gate, reactive ingestion, LLM-explains-not-decides |
| Automation depth & integration (15) | Discord + fulfillment webhook + MCP + ingestion |
| User experience & presentation (10) | Themed dashboard, live counters, toasts, this script |
