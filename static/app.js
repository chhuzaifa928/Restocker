"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const fmtMoney = (n) => "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });

const REASON_LABELS = {
  qty_over_limit: "Over qty cap", cost_over_hard_limit: "Over hard ceiling", over_budget: "Over budget",
  cost_over_limit: "Over auto limit", low_confidence: "Low confidence", unfamiliar_state: "Unfamiliar situation",
};
const STATUS_BADGE = {
  AUTO_APPROVED: ["badge-auto", "Auto-approved"], PENDING: ["badge-pending", "Pending"],
  HUMAN_APPROVED: ["badge-human_approved", "Approved"], HUMAN_REJECTED: ["badge-human_rejected", "Rejected"],
  BLOCKED: ["badge-blocked", "Blocked"],
};
const PAGES = {
  overview: ["Overview", "Live purchasing operations at a glance"],
  decisions: ["Decisions", "Every routed decision, newest first"],
  approvals: ["Approvals", "Orders waiting for human sign-off"],
  inventory: ["Inventory", "Live stock positions per product"],
  settings: ["Settings", "Tune the gate policy and view integrations"],
};
const resolving = new Set();
let currentPage = "overview";

// ---- helpers ------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  if (!el) return;
  const from = parseFloat(el.dataset.shown || "0");
  const fmt = (v) => opts.money ? fmtMoney(v) : (Math.round(v) + (opts.suffix || ""));
  if (from === to) { el.textContent = fmt(to); el.dataset.shown = to; return; }
  const kpi = el.closest(".kpi"); if (kpi) { kpi.classList.remove("bump"); void kpi.offsetWidth; kpi.classList.add("bump"); }
  const dur = 550, t0 = performance.now();
  (function step(now) {
    const p = Math.min((now - t0) / dur, 1), eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (to - from) * eased);
    if (p < 1) requestAnimationFrame(step); else { el.textContent = fmt(to); el.dataset.shown = to; }
  })(performance.now());
}

// ---- router -------------------------------------------------------------
function switchPage(name) {
  if (!PAGES[name]) name = "overview";
  currentPage = name;
  $$(".nav-item").forEach((n) => n.classList.toggle("is-active", n.dataset.page === name));
  $$(".page").forEach((p) => p.classList.add("hidden"));
  const page = $("#page-" + name);
  if (page) { page.classList.remove("hidden"); page.classList.remove("page"); void page.offsetWidth; page.classList.add("page"); }
  $("#page-title").textContent = PAGES[name][0];
  $("#page-sub").textContent = PAGES[name][1];
  document.querySelector(".app").classList.remove("nav-open");
  if (name === "settings") loadSettings();
}

// ---- metrics + charts ---------------------------------------------------
function renderMetrics(m) {
  animateNumber($("#k-total"), m.total_decisions);
  animateNumber($("#k-auto"), m.auto_approved_pct, { suffix: "%" });
  animateNumber($("#k-esc"), m.escalated);
  animateNumber($("#k-pending"), m.pending);
  animateNumber($("#k-orders"), m.orders_filled || 0);
  animateNumber($("#k-low"), m.low_stock);

  // nav badges
  toggleBadge($("#nav-pending"), m.pending);
  toggleBadge($("#nav-low"), m.low_stock);

  // donut + legend
  renderDonut([
    { name: "Auto-approved", value: m.auto_approved, color: "var(--c-auto)" },
    { name: "Escalated", value: m.escalated, color: "var(--c-esc)" },
    { name: "Blocked", value: m.blocked, color: "var(--c-block)" },
  ], m.total_decisions);

  // budget
  const spent = m.total_spend, init = m.initial_budget || 0;
  $("#ov-spent").textContent = fmtMoney(spent);
  $("#ov-remaining").textContent = fmtMoney(m.budget_remaining) + " remaining";
  $("#ov-initial").textContent = "of " + fmtMoney(init);
  $("#ov-budget-bar").style.width = (init ? Math.min(100, (spent / init) * 100) : 0) + "%";
  $("#ov-agree").textContent = (m.agreement_rate || 0) + "%";
  $("#ov-units").textContent = m.units_replenished || 0;
  $("#ov-gate").textContent = Number(m.effective_conf_threshold || 0.6).toFixed(2);

  // gate note (approvals)
  if (m.effective_conf_threshold != null) {
    $("#gate-eff").textContent = Number(m.effective_conf_threshold).toFixed(2);
    $("#gate-base").textContent = Number(m.base_conf_threshold).toFixed(2);
    $("#gate-note").classList.toggle("raised", m.effective_conf_threshold > m.base_conf_threshold + 1e-9);
  }

  const b = $("#banner");
  if (!m.model_loaded) { b.textContent = "⚠ Model not loaded — decisions unavailable until the Q-table is restored."; b.classList.remove("hidden"); }
  else b.classList.add("hidden");
}
function toggleBadge(el, n) { if (!el) return; if (n > 0) { el.textContent = n; el.classList.remove("hidden"); } else el.classList.add("hidden"); }

function renderDonut(segs, total) {
  const wrap = $("#donut-wrap"); if (!wrap) return;
  const r = 60, cx = 85, cy = 85, C = 2 * Math.PI * r, gap = total > 1 ? 4 : 0;
  let svg = `<svg viewBox="0 0 170 170" width="170" height="170">`;
  svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--surface-2)" stroke-width="22"/>`;
  if (total > 0) {
    let acc = 0;
    segs.forEach((s) => {
      if (s.value <= 0) return;
      const len = (s.value / total) * C;
      const dash = Math.max(0.1, len - gap);
      svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="22"
        stroke-dasharray="${dash} ${C - dash}" stroke-dashoffset="${-acc}"
        transform="rotate(-90 ${cx} ${cy})" stroke-linecap="butt"/>`;
      acc += len;
    });
  }
  svg += `</svg><div class="donut-center"><b>${total}</b><span>decisions</span></div>`;
  wrap.innerHTML = svg;

  const leg = $("#route-legend");
  leg.innerHTML = segs.map((s) => {
    const pct = total ? Math.round((s.value / total) * 100) : 0;
    return `<li><span class="sw" style="background:${s.color}"></span><span class="lg-name">${s.name}</span><span class="lg-val">${s.value} · ${pct}%</span></li>`;
  }).join("");
}

// ---- integrations (pills + settings rows) -------------------------------
let lastIntegrations = null;
async function loadIntegrations() {
  try {
    const s = await fetch("/api/model-status").then((r) => r.json());
    const ints = s.integrations || {};
    lastIntegrations = { ...ints, llm: s.llm_enabled, model: s.loaded };
    setPill($("#model-pill"), s.loaded, s.loaded ? "Model" : "No model", s.loaded ? "ok" : "bad");
    setPill($("#llm-pill"), s.llm_enabled, s.llm_enabled ? "LLM live" : "LLM fallback", s.llm_enabled ? "ok" : "warn");
    setPill($("#db-pill"), ints.persistence, ints.persistence ? "Store" : "Memory", ints.persistence ? "ok" : "muted");
    setPill($("#discord-pill"), ints.discord_notifications, "Discord", ints.discord_notifications ? "ok" : "muted");
    renderIntegrationRows(lastIntegrations);
  } catch (_) {}
}
function setPill(el, on, label, cls) {
  if (!el) return;
  el.className = "pill pill-" + (cls === "ok" ? "ok" : cls === "warn" ? "warn" : cls === "bad" ? "bad" : "muted");
  el.querySelector(".pill-label").textContent = label;
}
function renderIntegrationRows(i) {
  const box = $("#integrations"); if (!box || !i) return;
  const rows = [
    ["Pre-trained model", i.model], ["LLM rationales", i.llm], ["SQLite persistence", i.persistence],
    ["Discord notifications", i.discord_notifications], ["Order fulfillment webhook", i.order_fulfillment_webhook],
  ];
  box.innerHTML = rows.map(([name, on]) =>
    `<div class="int-row"><span class="int-dot ${on ? "int-on" : "int-off"}"></span>${name}
      <span class="int-state ${on ? "on" : "off"}">${on ? "ON" : "OFF"}</span></div>`).join("");
}

// ---- settings -----------------------------------------------------------
async function loadSettings() {
  try {
    const s = await fetch("/api/settings").then((r) => r.json());
    const f = $("#settings-form");
    Object.entries(s.runtime).forEach(([k, v]) => { if (f[k]) f[k].value = v; });
  } catch (_) {}
}
async function saveSettings(ev) {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    auto_limit: parseFloat(f.auto_limit.value), hard_limit: parseFloat(f.hard_limit.value),
    max_order_qty: parseInt(f.max_order_qty.value, 10), conf_threshold: parseFloat(f.conf_threshold.value),
    min_visits_familiar: parseInt(f.min_visits_familiar.value, 10),
  };
  try {
    const res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error(await res.text());
    toast("Gate policy saved — re-run a batch to see the effect", "ok");
    await refresh();
  } catch (e) { toast("Save failed: " + e.message, "err"); }
}
async function resetSettings() {
  try { await fetch("/api/settings/reset", { method: "POST" }); await loadSettings(); toast("Reset to default policy", "ok"); await refresh(); }
  catch (e) { toast("Reset failed: " + e.message, "err"); }
}

// ---- presets ------------------------------------------------------------
async function loadPresets() {
  try {
    const presets = await fetch("/api/presets").then((r) => r.json());
    const bar = $("#preset-bar");
    presets.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button"; btn.className = "preset-btn preset-" + p.key; btn.textContent = p.label; btn.title = p.hint;
      btn.addEventListener("click", () => runPreset(p, btn));
      bar.appendChild(btn);
    });
  } catch (_) {}
}
async function runPreset(p, btn) {
  btn.disabled = true;
  try {
    const res = await fetch("/api/evaluate", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: "DEMO-" + p.key + "-" + Date.now(), ...p.situation }) });
    if (!res.ok) throw new Error(await res.text());
    const d = await res.json();
    const where = d.route === "AUTO_APPROVED" ? "auto-executed" : d.route === "ESCALATED" ? "escalated" : "blocked";
    toast(`${d.product_name}: ${actionLabel(d.action)} ${d.quantity} → ${where}${d.po_number ? " · " + d.po_number : ""}`,
      d.route === "AUTO_APPROVED" ? "ok" : d.route === "BLOCKED" ? "err" : "warn");
    await refresh();
  } catch (e) { toast("Scenario failed: " + e.message, "err"); }
  finally { btn.disabled = false; }
}

// ---- decision feed + recent --------------------------------------------
function decisionRow(d) {
  const [badgeClass, badgeText] = STATUS_BADGE[d.status] || ["badge-auto", d.status];
  const confPct = Math.round(d.confidence * 100);
  const tip = `margin ${(d.confidence_margin * 100 || 0).toFixed(0)}% · familiarity ${(d.confidence_familiarity * 100 || 0).toFixed(0)}%`;
  const po = d.po_number ? `<span class="po-tag">${escapeHtml(d.po_number)}</span>` : "";
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <div class="row-product"><span class="row-name">${escapeHtml(d.product_name)}</span><span class="row-sub">${d.state.join(" · ")} ${po}</span></div>
    <div class="row-action">${actionLabel(d.action)}<small>${d.quantity} units</small></div>
    <div class="row-cost">${fmtMoney(d.cost)}</div>
    <div class="conf" title="${tip}"><div class="conf-top"><span>confidence</span><span>${confPct}%</span></div>
      <div class="conf-track"><div class="conf-fill" style="width:${confPct}%;background:${confColor(d.confidence)}"></div></div></div>
    <span class="badge ${badgeClass}">${badgeText}</span>`;
  return row;
}
function renderFeed(decisions) {
  const feed = $("#feed"); feed.innerHTML = "";
  if (!decisions.length) { feed.innerHTML = '<div class="empty"><p>No decisions yet.</p><p class="muted">Click <strong>Run batch</strong>.</p></div>'; return; }
  decisions.forEach((d) => feed.appendChild(decisionRow(d)));
}
function renderRecent(decisions) {
  const box = $("#recent"); if (!box) return;
  const items = decisions.slice(0, 6);
  if (!items.length) { box.innerHTML = '<div class="empty"><p class="muted">Run a batch to see decisions here.</p></div>'; return; }
  box.innerHTML = items.map((d) => {
    const [bc, bt] = STATUS_BADGE[d.status] || ["badge-auto", d.status];
    return `<div class="recent-row"><div><div class="rr-name">${escapeHtml(d.product_name)}</div>
      <div class="rr-sub">${actionLabel(d.action)} · ${d.quantity} units</div></div>
      <div class="rr-cost">${fmtMoney(d.cost)}</div><div>${Math.round(d.confidence * 100)}%</div>
      <span class="badge ${bc}">${bt}</span></div>`;
  }).join("");
}

// ---- approval queue -----------------------------------------------------
function queueCard(d) {
  const card = document.createElement("div");
  card.className = "card"; card.dataset.id = d.id;
  const chips = d.reasons.map((r) => `<span class="chip">${REASON_LABELS[r] || r}</span>`).join("");
  const isFallback = d.rationale_source === "fallback";
  card.innerHTML = `
    <div class="card-head"><div class="card-title">${escapeHtml(d.product_name)}</div><span class="badge badge-escalated">Escalated</span></div>
    <div class="chips">${chips}</div>
    <div class="rationale"><span class="tag ${isFallback ? "fallback" : ""}">${isFallback ? "Rationale · fallback template" : "AI rationale"}</span> ${escapeHtml(d.rationale)}</div>
    <div class="card-numbers">
      <div class="num"><b>${actionLabel(d.action)}</b><span>action</span></div>
      <div class="num"><b>${d.quantity}</b><span>units</span></div>
      <div class="num"><b>${fmtMoney(d.cost)}</b><span>cost</span></div>
      <div class="num"><b>${Math.round(d.confidence * 100)}%</b><span>confidence</span></div></div>
    <div class="card-actions"><button class="btn btn-approve" data-act="approve">Approve</button><button class="btn btn-reject" data-act="reject">Reject</button></div>`;
  card.querySelectorAll("button[data-act]").forEach((btn) => btn.addEventListener("click", () => resolve(d, btn.dataset.act, card)));
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
  queue.querySelectorAll(".card").forEach((c) => { if (!want.has(c.dataset.id) && !c.classList.contains("leaving")) c.remove(); });
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
    if (act === "approve") toast(`Approved · ${d.product_name} (${d.quantity} units, ${fmtMoney(d.cost)})${updated.po_number ? " · PO " + updated.po_number : ""}`, "ok");
    else toast(`Rejected · ${d.product_name} voided`, "warn");
    setTimeout(() => { card.remove(); resolving.delete(d.id); refresh(); if (!$("#queue").querySelector(".card")) $("#queue-empty").classList.remove("hidden"); }, 400);
  } catch (e) { resolving.delete(d.id); card.querySelectorAll("button").forEach((b) => (b.disabled = false)); toast("Action failed: " + e.message, "err"); }
}

// ---- inventory ----------------------------------------------------------
const INV_LAST_BADGE = { AUTO_APPROVED: ["badge-auto", "auto"], ESCALATED: ["badge-escalated", "escalated"], BLOCKED: ["badge-blocked", "blocked"], HUMAN_APPROVED: ["badge-human_approved", "approved"] };
function invCard(p) {
  const card = document.createElement("div"); card.dataset.pid = p.product_id;
  card.innerHTML = `
    <div class="inv-top"><div><div class="inv-name"></div><div class="inv-sku"></div></div><span class="inv-status"></span></div>
    <div class="inv-stock"><b class="inv-qty">0</b><span>units in stock</span></div>
    <div class="stock-bar"><div class="stock-fill"></div><div class="reorder-mark"></div></div>
    <div class="inv-meta"><div><b class="inv-fc"></b><span>forecast</span></div><div><b class="inv-price"></b><span>unit price</span></div><div><b class="inv-cov"></b><span>coverage</span></div></div>
    <div class="inv-last"></div>
    <div class="inv-actions"><button class="btn btn-reorder" data-act="reorder">Evaluate reorder</button>
      <div class="stepper"><button data-step="-25" title="Remove 25">−</button><button data-step="25" title="Receive 25">+</button></div></div>`;
  card.querySelector('[data-act="reorder"]').addEventListener("click", () => reorder(p.product_id));
  card.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => adjust(p.product_id, parseFloat(b.dataset.step))));
  updateInvCard(card, p); return card;
}
function updateInvCard(card, p) {
  const s = p.status.toLowerCase();
  card.className = "inv-card s-" + s;
  animateNumber(card.querySelector(".inv-qty"), p.inventory_level);
  card.querySelector(".inv-name").textContent = p.product_name;
  card.querySelector(".inv-sku").textContent = p.product_id;
  const chip = card.querySelector(".inv-status"); chip.className = "inv-status s-" + s; chip.textContent = p.status;
  const fill = card.querySelector(".stock-fill"); fill.className = "stock-fill s-" + s; fill.style.width = p.stock_pct + "%";
  card.querySelector(".reorder-mark").style.left = "50%";
  card.querySelector(".inv-fc").textContent = Math.round(p.demand_forecast);
  card.querySelector(".inv-price").textContent = fmtMoney(p.unit_price);
  card.querySelector(".inv-cov").textContent = p.coverage.toFixed(2) + "×";
  const last = card.querySelector(".inv-last");
  if (p.last_action) { const [bc, bt] = INV_LAST_BADGE[p.last_route] || ["badge-pending", (p.last_route || "").toLowerCase()];
    last.innerHTML = `last: ${actionLabel(p.last_action)} · <span class="badge mini-badge ${bc}">${bt}</span>`; }
  else last.innerHTML = `<span class="muted">no decision yet</span>`;
}
function renderInventory(items) {
  const grid = $("#inv-grid");
  if (!items.length) return;
  const firstEmpty = grid.querySelector(".empty"); if (firstEmpty) firstEmpty.remove();
  const have = {}; grid.querySelectorAll(".inv-card").forEach((c) => (have[c.dataset.pid] = c));
  items.forEach((p, i) => { if (have[p.product_id]) updateInvCard(have[p.product_id], p);
    else { const c = invCard(p); c.style.animationDelay = (i * 0.03) + "s"; grid.appendChild(c); } delete have[p.product_id]; });
  Object.values(have).forEach((c) => c.remove());
}
async function reorder(pid) {
  try {
    const res = await fetch(`/api/inventory/${pid}/reorder`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const d = await res.json();
    const where = d.route === "AUTO_APPROVED" ? "auto-executed" : d.route === "ESCALATED" ? "escalated for approval" : "blocked";
    toast(`${d.product_name}: ${actionLabel(d.action)} ${d.quantity} → ${where}`, d.route === "AUTO_APPROVED" ? "ok" : d.route === "BLOCKED" ? "err" : "warn");
    flashCard(pid); await refresh();
  } catch (e) { toast("Reorder failed: " + e.message, "err"); }
}
async function adjust(pid, delta) {
  try {
    const res = await fetch(`/api/inventory/${pid}/adjust`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delta }) });
    if (!res.ok) throw new Error(await res.text());
    flashCard(pid); await refresh();
  } catch (e) { toast("Adjust failed: " + e.message, "err"); }
}
function flashCard(pid) { const c = $(`.inv-card[data-pid="${CSS.escape(pid)}"]`); if (c) { c.classList.remove("flash"); void c.offsetWidth; c.classList.add("flash"); } }

// ---- actions ------------------------------------------------------------
async function runBatch() {
  const btn = $("#run-btn"); btn.disabled = true;
  btn.querySelector(".btn-spinner").classList.remove("hidden"); btn.querySelector(".btn-text").textContent = "Running…";
  try {
    const res = await fetch("/api/run-batch", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const ds = await res.json();
    $("#queue").innerHTML = '<div class="empty queue-empty hidden" id="queue-empty"></div>';
    const c = (r) => ds.filter((d) => d.route === r).length;
    toast(`Batch complete · ${c("AUTO_APPROVED")} auto · ${c("ESCALATED")} escalated · ${c("BLOCKED")} blocked`, "ok");
    await refresh();
  } catch (e) { toast("Run batch failed: " + e.message, "err"); }
  finally { btn.disabled = false; btn.querySelector(".btn-spinner").classList.add("hidden"); btn.querySelector(".btn-text").textContent = "Run batch"; }
}
async function ingestDemand() {
  const btn = $("#ingest-btn"); btn.disabled = true;
  btn.querySelector(".btn-spinner").classList.remove("hidden"); btn.querySelector(".btn-text").textContent = "Ingesting…";
  try {
    const res = await fetch("/api/ingest", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json(); const ds = data.decisions || [];
    const c = (r) => ds.filter((d) => d.route === r).length;
    toast(`📡 ${data.source} signal · ${ds.length} re-evaluated · ${c("AUTO_APPROVED")} auto · ${c("ESCALATED")} escalated · ${c("BLOCKED")} blocked`, (c("ESCALATED") || c("BLOCKED")) ? "warn" : "ok", 4200);
    await refresh();
  } catch (e) { toast("Ingest failed: " + e.message, "err"); }
  finally { btn.disabled = false; btn.querySelector(".btn-spinner").classList.add("hidden"); btn.querySelector(".btn-text").textContent = "📡 Ingest demand"; }
}
async function addSituation(ev) {
  ev.preventDefault(); const f = ev.target;
  try {
    const res = await fetch("/api/evaluate", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: "MANUAL-" + Date.now(), product_name: f.product_name.value.trim(),
        inventory_level: parseFloat(f.inventory_level.value), demand_forecast: parseFloat(f.demand_forecast.value), unit_price: parseFloat(f.unit_price.value) }) });
    if (!res.ok) throw new Error(await res.text());
    const d = await res.json();
    toast(`${d.product_name} → ${d.route.replace("_", " ").toLowerCase()}`, d.route === "AUTO_APPROVED" ? "ok" : d.route === "BLOCKED" ? "err" : "warn");
    f.reset(); await refresh();
  } catch (e) { toast("Evaluate failed: " + e.message, "err"); }
}

// ---- theme --------------------------------------------------------------
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("restock-theme", next); } catch (_) {}
}

// ---- polling ------------------------------------------------------------
let refreshSeq = 0;
async function refresh() {
  const mySeq = ++refreshSeq;
  try {
    const [decisions, pending, metrics, inv] = await Promise.all([
      fetch("/api/decisions").then((r) => r.json()), fetch("/api/pending").then((r) => r.json()),
      fetch("/api/metrics").then((r) => r.json()), fetch("/api/inventory").then((r) => r.json()),
    ]);
    if (mySeq !== refreshSeq) return;
    renderMetrics(metrics); renderFeed(decisions); renderRecent(decisions); renderQueue(pending); renderInventory(inv);
  } catch (_) {}
}

// ---- wire up ------------------------------------------------------------
$$(".nav-item").forEach((n) => n.addEventListener("click", () => switchPage(n.dataset.page)));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => switchPage(b.dataset.goto)));
$("#menu-btn").addEventListener("click", () => document.querySelector(".app").classList.toggle("nav-open"));
$("#scrim").addEventListener("click", () => document.querySelector(".app").classList.remove("nav-open"));
$("#theme-btn").addEventListener("click", toggleTheme);
$("#run-btn").addEventListener("click", runBatch);
$("#ingest-btn").addEventListener("click", ingestDemand);
$("#export-btn").addEventListener("click", () => { window.location.href = "/api/decisions.csv"; });
$("#add-toggle").addEventListener("click", () => $("#add-form").classList.toggle("hidden"));
$("#add-form").addEventListener("submit", addSituation);
$("#settings-form").addEventListener("submit", saveSettings);
$("#settings-reset").addEventListener("click", resetSettings);

loadIntegrations();
loadPresets();
switchPage("overview");
refresh();
setInterval(refresh, 2500);
setInterval(loadIntegrations, 15000);
