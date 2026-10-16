"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const fmtMoney = (n) => "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });

const REASON_LABELS = {
  qty_over_limit: "Over qty cap",
  cost_over_hard_limit: "Over hard ceiling",
  over_budget: "Over budget",
  cost_over_limit: "Over $5k limit",
  low_confidence: "Low confidence",
  unfamiliar_state: "Unfamiliar situation",
};
const STATUS_BADGE = {
  AUTO_APPROVED: ["badge-auto", "Auto-approved"],
  PENDING: ["badge-pending", "Pending"],
  HUMAN_APPROVED: ["badge-human_approved", "Approved"],
  HUMAN_REJECTED: ["badge-human_rejected", "Rejected"],
  BLOCKED: ["badge-blocked", "Blocked"],
};

const resolving = new Set();

// ---- helpers ------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function confColor(c) { return c >= 0.6 ? "var(--green)" : c >= 0.4 ? "var(--amber)" : "var(--red)"; }
function actionLabel(a) { return a.replace("BUY_", "").replace("_", " ").toLowerCase(); }

function toast(msg, type = "info", ms = 3200) {
  const icons = { ok: "✓", info: "i", warn: "!", err: "✕" };
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.innerHTML = `<span class="ticon">${icons[type] || "i"}</span><span>${escapeHtml(msg)}</span>`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("leaving"); setTimeout(() => el.remove(), 300); }, ms);
}

function animateNumber(el, to, opts = {}) {
  const from = parseFloat(el.dataset.shown || "0");
  const fmt = (v) => opts.money ? fmtMoney(v) : (Math.round(v) + (opts.suffix || ""));
  if (from === to) { el.textContent = fmt(to); el.dataset.shown = to; return; }
  if (el.closest(".metric")) { const m = el.closest(".metric"); m.classList.remove("bump"); void m.offsetWidth; m.classList.add("bump"); }
  const dur = 550, t0 = performance.now();
  function step(now) {
    const p = Math.min((now - t0) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (to - from) * eased);
    if (p < 1) requestAnimationFrame(step); else { el.textContent = fmt(to); el.dataset.shown = to; }
  }
  requestAnimationFrame(step);
}

// ---- metrics ------------------------------------------------------------
function renderMetrics(m) {
  animateNumber($("#m-total"), m.total_decisions);
  animateNumber($("#m-auto"), m.auto_approved_pct, { suffix: "%" });
  animateNumber($("#m-esc"), m.escalated);
  animateNumber($("#m-pending"), m.pending);
  animateNumber($("#m-low"), m.low_stock);
  animateNumber($("#m-orders"), m.orders_filled || 0);
  animateNumber($("#m-agree"), m.agreement_rate || 0, { suffix: "%" });
  animateNumber($("#m-budget"), m.budget_remaining, { money: true });
  const pct = m.initial_budget ? Math.max(0, Math.min(100, (m.budget_remaining / m.initial_budget) * 100)) : 0;
  $("#m-budget-bar").style.width = pct + "%";

  const lowBadge = $("#tab-low-badge");
  if (m.low_stock > 0) { lowBadge.textContent = m.low_stock; lowBadge.classList.remove("hidden"); }
  else lowBadge.classList.add("hidden");

  const model = $("#model-pill");
  model.className = "pill " + (m.model_loaded ? "pill-ok" : "pill-bad");
  model.querySelector(".pill-label").textContent = m.model_loaded ? "Model loaded" : "Model missing";
  const llm = $("#llm-pill");
  llm.className = "pill " + (m.llm_enabled ? "pill-ok" : "pill-warn");
  llm.querySelector(".pill-label").textContent = m.llm_enabled ? "LLM live" : "LLM fallback";

  // adaptive gate readout (human-feedback loop)
  if (m.effective_conf_threshold != null) {
    $("#gate-eff").textContent = Number(m.effective_conf_threshold).toFixed(2);
    $("#gate-base").textContent = Number(m.base_conf_threshold).toFixed(2);
    const raised = m.effective_conf_threshold > m.base_conf_threshold + 1e-9;
    $("#gate-note").classList.toggle("raised", raised);
  }

  const b = $("#banner");
  if (!m.model_loaded) { b.textContent = "⚠ Model not loaded — decisions are unavailable until the Q-table is restored."; b.classList.remove("hidden"); }
  else b.classList.add("hidden");
}

// ---- integrations (static, from model-status) ---------------------------
async function loadIntegrations() {
  try {
    const s = await fetch("/api/model-status").then((r) => r.json());
    const ints = s.integrations || {};
    const db = $("#db-pill");
    db.className = "pill " + (ints.persistence ? "pill-ok" : "pill-muted");
    db.querySelector(".pill-label").textContent = ints.persistence ? "Persisted" : "In-memory";
    const dc = $("#discord-pill");
    dc.className = "pill " + (ints.discord_notifications ? "pill-ok" : "pill-muted");
    dc.querySelector(".pill-label").textContent = ints.discord_notifications ? "Discord on" : "Discord off";
  } catch (_) {}
}

// ---- demo scenario presets ----------------------------------------------
async function loadPresets() {
  try {
    const presets = await fetch("/api/presets").then((r) => r.json());
    const bar = $("#preset-bar");
    presets.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "preset-btn preset-" + p.key;
      btn.textContent = p.label;
      btn.title = p.hint;
      btn.addEventListener("click", () => runPreset(p, btn));
      bar.appendChild(btn);
    });
  } catch (_) {}
}
async function runPreset(p, btn) {
  btn.disabled = true;
  try {
    const res = await fetch("/api/evaluate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product_id: "DEMO-" + p.key + "-" + Date.now(),
        product_name: p.situation.product_name,
        inventory_level: p.situation.inventory_level,
        demand_forecast: p.situation.demand_forecast,
        unit_price: p.situation.unit_price,
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const d = await res.json();
    const where = d.route === "AUTO_APPROVED" ? "auto-executed" :
                  d.route === "ESCALATED" ? "escalated for approval" : "blocked";
    const po = d.po_number ? ` · ${d.po_number}` : "";
    toast(`${d.product_name}: ${actionLabel(d.action)} ${d.quantity} → ${where}${po}`,
          d.route === "AUTO_APPROVED" ? "ok" : d.route === "BLOCKED" ? "err" : "warn");
    switchView("decisions");
    await refresh();
  } catch (e) { toast("Scenario failed: " + e.message, "err"); }
  finally { btn.disabled = false; }
}

// ---- decision feed ------------------------------------------------------
function decisionRow(d) {
  const [badgeClass, badgeText] = STATUS_BADGE[d.status] || ["badge-auto", d.status];
  const confPct = Math.round(d.confidence * 100);
  const confTip = `margin ${(d.confidence_margin * 100 || 0).toFixed(0)}% · familiarity ${(d.confidence_familiarity * 100 || 0).toFixed(0)}%`;
  const po = d.po_number ? `<span class="po-tag" title="Purchase order issued${d.fulfillment === "webhook" ? " · sent to webhook" : ""}">${escapeHtml(d.po_number)}</span>` : "";
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <div class="row-product">
      <span class="row-name">${escapeHtml(d.product_name)}</span>
      <span class="row-sub">${d.state.join(" · ")} ${po}</span>
    </div>
    <div class="row-action">${actionLabel(d.action)}<small>${d.quantity} units</small></div>
    <div class="row-cost">${fmtMoney(d.cost)}</div>
    <div class="conf" title="${confTip}">
      <div class="conf-top"><span>confidence</span><span>${confPct}%</span></div>
      <div class="conf-track"><div class="conf-fill" style="width:${confPct}%;background:${confColor(d.confidence)}"></div></div>
    </div>
    <span class="badge ${badgeClass}">${badgeText}</span>`;
  return row;
}
const FEED_EMPTY_HTML =
  '<div class="empty"><p>No decisions yet.</p>' +
  '<p class="muted">Click <strong>Run batch</strong> to let the agent clear the queue.</p></div>';

function renderFeed(decisions) {
  const feed = $("#feed");
  feed.innerHTML = "";  // rebuild from scratch; recreate empty state as needed
  if (!decisions.length) { feed.innerHTML = FEED_EMPTY_HTML; return; }
  decisions.forEach((d) => feed.appendChild(decisionRow(d)));
}

// ---- approval queue -----------------------------------------------------
function queueCard(d) {
  const card = document.createElement("div");
  card.className = "card"; card.dataset.id = d.id;
  const chips = d.reasons.map((r) => `<span class="chip">${REASON_LABELS[r] || r}</span>`).join("");
  const isFallback = d.rationale_source === "fallback";
  card.innerHTML = `
    <div class="card-head">
      <div class="card-title">${escapeHtml(d.product_name)}</div>
      <span class="badge badge-escalated">Escalated</span>
    </div>
    <div class="chips">${chips}</div>
    <div class="rationale">
      <span class="tag ${isFallback ? "fallback" : ""}">${isFallback ? "Rationale · fallback template" : "AI rationale"}</span>
      ${escapeHtml(d.rationale)}
    </div>
    <div class="card-numbers">
      <div class="num"><b>${actionLabel(d.action)}</b><span>action</span></div>
      <div class="num"><b>${d.quantity}</b><span>units</span></div>
      <div class="num"><b>${fmtMoney(d.cost)}</b><span>cost</span></div>
      <div class="num"><b>${Math.round(d.confidence * 100)}%</b><span>confidence</span></div>
    </div>
    <div class="card-actions">
      <button class="btn btn-approve" data-act="approve">Approve</button>
      <button class="btn btn-reject" data-act="reject">Reject</button>
    </div>`;
  card.querySelectorAll("button[data-act]").forEach((btn) =>
    btn.addEventListener("click", () => resolve(d, btn.dataset.act, card)));
  return card;
}
function renderQueue(pending) {
  const queue = $("#queue"), empty = $("#queue-empty");
  $("#queue-count").textContent = pending.length;
  const visible = pending.filter((d) => !resolving.has(d.id));
  if (!visible.length) { if (empty && !queue.querySelector(".card")) empty.classList.remove("hidden"); return; }
  if (empty) empty.classList.add("hidden");
  const have = new Set([...queue.querySelectorAll(".card")].map((c) => c.dataset.id));
  const want = new Set(visible.map((d) => d.id));
  queue.querySelectorAll(".card").forEach((c) => {
    if (!want.has(c.dataset.id) && !c.classList.contains("leaving")) c.remove();
  });
  visible.forEach((d) => { if (!have.has(d.id)) queue.appendChild(queueCard(d)); });
}

async function resolve(d, act, card) {
  resolving.add(d.id);
  card.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const res = await fetch(`/api/decisions/${d.id}/${act}`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const updated = await res.json().catch(() => ({}));
    card.classList.add("leaving");
    if (act === "approve") {
      const po = updated.po_number ? ` · PO ${updated.po_number}` : "";
      toast(`Approved · ${d.product_name} (${d.quantity} units, ${fmtMoney(d.cost)})${po}`, "ok");
    } else toast(`Rejected · ${d.product_name} voided`, "warn");
    setTimeout(() => {
      card.remove(); resolving.delete(d.id); refresh();
      if (!$("#queue").querySelector(".card")) $("#queue-empty").classList.remove("hidden");
    }, 400);
  } catch (e) {
    resolving.delete(d.id);
    card.querySelectorAll("button").forEach((b) => (b.disabled = false));
    toast("Action failed: " + e.message, "err");
  }
}

// ---- inventory ----------------------------------------------------------
const INV_LAST_BADGE = {
  AUTO_APPROVED: ["badge-auto", "auto"],
  ESCALATED: ["badge-escalated", "escalated"],
  BLOCKED: ["badge-blocked", "blocked"],
  HUMAN_APPROVED: ["badge-human_approved", "approved"],
};
function invCard(p) {
  const card = document.createElement("div");
  card.dataset.pid = p.product_id;
  card.innerHTML = `
    <div class="inv-top">
      <div><div class="inv-name"></div><div class="inv-sku"></div></div>
      <span class="inv-status"></span>
    </div>
    <div class="inv-stock"><b class="inv-qty">0</b><span>units in stock</span></div>
    <div class="stock-bar"><div class="stock-fill"></div><div class="reorder-mark"></div></div>
    <div class="inv-meta">
      <div><b class="inv-fc"></b><span>forecast</span></div>
      <div><b class="inv-price"></b><span>unit price</span></div>
      <div><b class="inv-cov"></b><span>coverage</span></div>
    </div>
    <div class="inv-last"></div>
    <div class="inv-actions">
      <button class="btn btn-reorder" data-act="reorder">Evaluate reorder</button>
      <div class="stepper">
        <button data-step="-25" title="Remove 25 (shrinkage / sale)">−</button>
        <button data-step="25" title="Receive 25 (manual restock)">+</button>
      </div>
    </div>`;
  card.querySelector('[data-act="reorder"]').addEventListener("click", () => reorder(p.product_id));
  card.querySelectorAll("[data-step]").forEach((b) =>
    b.addEventListener("click", () => adjust(p.product_id, parseFloat(b.dataset.step))));
  updateInvCard(card, p);
  return card;
}
function updateInvCard(card, p) {
  const s = p.status.toLowerCase();
  card.className = "inv-card s-" + s;
  const qty = card.querySelector(".inv-qty");
  animateNumber(qty, p.inventory_level);
  card.querySelector(".inv-name").textContent = p.product_name;
  card.querySelector(".inv-sku").textContent = p.product_id;
  const chip = card.querySelector(".inv-status");
  chip.className = "inv-status s-" + s;
  chip.textContent = p.status;
  const fill = card.querySelector(".stock-fill");
  fill.className = "stock-fill s-" + s;
  fill.style.width = p.stock_pct + "%";
  // reorder point marker (at 0.75*forecast relative to bar target forecast*1.5 => 50%)
  card.querySelector(".reorder-mark").style.left = "50%";
  card.querySelector(".inv-fc").textContent = Math.round(p.demand_forecast);
  card.querySelector(".inv-price").textContent = fmtMoney(p.unit_price);
  card.querySelector(".inv-cov").textContent = p.coverage.toFixed(2) + "×";
  const last = card.querySelector(".inv-last");
  if (p.last_action) {
    const [bc, bt] = INV_LAST_BADGE[p.last_route] || ["badge-pending", (p.last_route || "").toLowerCase()];
    last.innerHTML = `last: ${actionLabel(p.last_action)} · <span class="badge mini-badge ${bc}">${bt}</span>`;
  } else {
    last.innerHTML = `<span class="muted">no decision yet</span>`;
  }
}
function renderInventory(items) {
  const grid = $("#inv-grid"), empty = $("#inv-empty");
  if (!items.length) { if (empty) empty.classList.remove("hidden"); return; }
  if (empty) empty.classList.add("hidden");
  const have = {};
  grid.querySelectorAll(".inv-card").forEach((c) => (have[c.dataset.pid] = c));
  items.forEach((p, i) => {
    if (have[p.product_id]) updateInvCard(have[p.product_id], p);
    else { const c = invCard(p); c.style.animationDelay = (i * 0.03) + "s"; grid.appendChild(c); }
    delete have[p.product_id];
  });
  Object.values(have).forEach((c) => c.remove());
}

async function reorder(pid) {
  try {
    const res = await fetch(`/api/inventory/${pid}/reorder`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const d = await res.json();
    const where = d.route === "AUTO_APPROVED" ? "auto-executed" :
                  d.route === "ESCALATED" ? "escalated for approval" : "blocked";
    toast(`${d.product_name}: ${actionLabel(d.action)} ${d.quantity} units → ${where}`,
          d.route === "AUTO_APPROVED" ? "ok" : d.route === "BLOCKED" ? "err" : "warn");
    flashCard(pid);
    await refresh();
  } catch (e) { toast("Reorder failed: " + e.message, "err"); }
}
async function adjust(pid, delta) {
  try {
    const res = await fetch(`/api/inventory/${pid}/adjust`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delta }),
    });
    if (!res.ok) throw new Error(await res.text());
    flashCard(pid);
    await refresh();
  } catch (e) { toast("Adjust failed: " + e.message, "err"); }
}
function flashCard(pid) {
  const c = $(`.inv-card[data-pid="${CSS.escape(pid)}"]`);
  if (c) { c.classList.remove("flash"); void c.offsetWidth; c.classList.add("flash"); }
}

// ---- actions: run batch / add situation ---------------------------------
async function runBatch() {
  const btn = $("#run-btn");
  btn.disabled = true;
  btn.querySelector(".btn-spinner").classList.remove("hidden");
  btn.querySelector(".btn-text").textContent = "Running…";
  try {
    const res = await fetch("/api/run-batch", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const ds = await res.json();
    $("#queue").innerHTML = '<div class="empty queue-empty hidden" id="queue-empty"></div>';
    const auto = ds.filter((d) => d.route === "AUTO_APPROVED").length;
    const esc = ds.filter((d) => d.route === "ESCALATED").length;
    const blk = ds.filter((d) => d.route === "BLOCKED").length;
    toast(`Batch complete · ${auto} auto · ${esc} escalated · ${blk} blocked`, "ok");
    await refresh();
  } catch (e) { toast("Run batch failed: " + e.message, "err"); }
  finally {
    btn.disabled = false;
    btn.querySelector(".btn-spinner").classList.add("hidden");
    btn.querySelector(".btn-text").textContent = "Run batch";
  }
}
async function addSituation(ev) {
  ev.preventDefault();
  const f = ev.target;
  try {
    const res = await fetch("/api/evaluate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product_id: "MANUAL-" + Date.now(),
        product_name: f.product_name.value.trim(),
        inventory_level: parseFloat(f.inventory_level.value),
        demand_forecast: parseFloat(f.demand_forecast.value),
        unit_price: parseFloat(f.unit_price.value),
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const d = await res.json();
    toast(`${d.product_name} → ${d.route.replace("_", " ").toLowerCase()}`,
          d.route === "AUTO_APPROVED" ? "ok" : d.route === "BLOCKED" ? "err" : "warn");
    f.reset();
    await refresh();
  } catch (e) { toast("Evaluate failed: " + e.message, "err"); }
}

// ---- tabs ---------------------------------------------------------------
function switchView(view) {
  $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === view));
  $("#view-decisions").classList.toggle("hidden", view !== "decisions");
  $("#view-inventory").classList.toggle("hidden", view !== "inventory");
  if (view === "inventory") { const v = $("#view-inventory"); v.classList.remove("view"); void v.offsetWidth; v.classList.add("view"); }
}

// ---- polling ------------------------------------------------------------
// Guard against overlapping refreshes rendering out of order: only the most
// recently started refresh is allowed to paint. Without this, a poll already
// in flight can resolve after an action's refresh and overwrite the feed with
// stale data (dropping a just-evaluated decision until the next poll).
let refreshSeq = 0;
async function refresh() {
  const mySeq = ++refreshSeq;
  try {
    const [decisions, pending, metrics, inv] = await Promise.all([
      fetch("/api/decisions").then((r) => r.json()),
      fetch("/api/pending").then((r) => r.json()),
      fetch("/api/metrics").then((r) => r.json()),
      fetch("/api/inventory").then((r) => r.json()),
    ]);
    if (mySeq !== refreshSeq) return; // a newer refresh superseded this one
    renderMetrics(metrics);
    renderFeed(decisions);
    renderQueue(pending);
    renderInventory(inv);
  } catch (_) {}
}

// ---- wire up ------------------------------------------------------------
// ---- theme toggle -------------------------------------------------------
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("restock-theme", next); } catch (_) {}
}

$("#theme-btn").addEventListener("click", toggleTheme);
$("#run-btn").addEventListener("click", runBatch);
$("#add-form").addEventListener("submit", addSituation);
$("#add-toggle").addEventListener("click", () => $("#add-form").classList.toggle("hidden"));
$("#export-btn").addEventListener("click", () => { window.location.href = "/api/decisions.csv"; });
$$(".tab").forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));

loadIntegrations();
loadPresets();
refresh();
setInterval(refresh, 2500);
