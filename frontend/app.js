/* TrackBox frontend — vanilla JS, no build step. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const form = $("#track-form");
const numberInput = $("#number");
const carrierInput = $("#carrier-input");
const carrierCode = $("#carrier-code");
const carrierList = $("#carrier-list");
const trackBtn = $("#track-btn");
const stateEl = $("#state");
const resultEl = $("#result");

// status → { color CSS var, icon glyph }
const STATUS_META = {
  Delivered:          { color: "var(--st-delivered)", icon: "✓" },
  InTransit:          { color: "var(--st-transit)",   icon: "🚚" },
  OutForDelivery:     { color: "var(--st-out)",       icon: "📬" },
  InfoReceived:       { color: "var(--st-info)",      icon: "📝" },
  AvailableForPickup: { color: "var(--st-pickup)",    icon: "🏪" },
  DeliveryFailure:    { color: "var(--st-fail)",      icon: "⚠️" },
  Exception:          { color: "var(--st-exception)", icon: "❗" },
  Expired:            { color: "var(--st-info)",      icon: "⏳" },
  NotFound:           { color: "var(--st-notfound)",  icon: "❓" },
};
const META = (s) => STATUS_META[s] || STATUS_META.NotFound;

/* ── time helpers ──────────────────────────────────────────────────────── */
function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}
function fmtAbs(iso) {
  const d = parseDate(iso);
  return d ? d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}
function fmtRel(iso) {
  const d = parseDate(iso);
  if (!d) return "";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (Math.abs(mins) < 60) return rel(mins, "minute");
  const hrs = Math.round(mins / 60);
  if (Math.abs(hrs) < 24) return rel(hrs, "hour");
  return rel(Math.round(hrs / 24), "day");
}
function rel(value, unit) {
  try { return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(-value, unit); }
  catch { return `${Math.abs(value)} ${unit}${Math.abs(value) === 1 ? "" : "s"} ago`; }
}
function fmtDate(iso) {
  const d = parseDate(iso);
  return d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ── UI state ──────────────────────────────────────────────────────────── */
function setLoading(on) {
  trackBtn.disabled = on;
  $(".btn-label", trackBtn).textContent = on ? "Tracking" : "Track";
  $(".spinner", trackBtn).hidden = !on;
}

function showLoader() {
  resultEl.hidden = true;
  stateEl.hidden = false;
  stateEl.className = "state";
  stateEl.innerHTML = `
    <div class="loader">
      <div class="loader-orbit">
        <span class="ring r1"></span><span class="ring r2"></span><span class="ring r3"></span>
        <span class="box">📦</span>
      </div>
      <div class="loader-text">Locating your package<span class="dots"></span></div>
    </div>`;
}

function showState(html, isError = false) {
  resultEl.hidden = true;
  stateEl.hidden = false;
  stateEl.className = "state" + (isError ? " error" : "");
  stateEl.innerHTML = html;
}

function locationText(addr, fallback) {
  if (!addr) return fallback || "—";
  const parts = [addr.city, addr.state, addr.country].filter(Boolean);
  return parts.length ? parts.join(", ") : (fallback || "—");
}

/* ── rendering ─────────────────────────────────────────────────────────── */
function render(result) {
  stateEl.hidden = true;
  resultEl.hidden = false;
  const meta = META(result.status);
  resultEl.style.setProperty("--accent-status", meta.color);

  resultEl.innerHTML = [
    renderStatusCard(result, meta),
    renderMilestones(result.milestones || []),
    renderTimeline(result),
  ].join("");

  bindResultEvents();
  requestAnimationFrame(() => {
    animateMilestoneFill();
    countUp();
  });
  if (result.is_delivered && !REDUCED) celebrate(meta.color);
}

function statusIcon(result, meta) {
  if (result.is_delivered) {
    return `<span class="badge-icon"><svg class="check-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5 9.5 18 20 6"/></svg></span>`;
  }
  return `<span class="badge-icon">${meta.icon}</span>`;
}

function renderStatusCard(r, meta) {
  const carrier = r.carrier || {};
  const carrierName = carrier.name || "Carrier";
  const sub = r.sub_status ? `<div class="status-sub">${esc(r.sub_status)}</div>` : "";

  const eta = r.estimated_delivery;
  const etaText = eta
    ? (eta.from && eta.to && fmtDate(eta.from) !== fmtDate(eta.to)
        ? `${fmtDate(eta.from)} – ${fmtDate(eta.to)}` : fmtDate(eta.to || eta.from))
    : null;
  const m = r.metrics || {};
  const chips = [
    etaText ? metric(r.is_delivered ? "Delivered on" : "Est. delivery", etaText) : "",
    Number.isInteger(m.days_of_transit) ? metric("Days in transit", m.days_of_transit, true) : "",
    r.latest_event?.time_utc ? metric("Last update", fmtRel(r.latest_event.time_utc)) : "",
  ].filter(Boolean).join("");

  return `<div class="card status-card">
    <div class="status-top">
      <div>
        <span class="status-badge">${statusIcon(r, meta)}${esc(r.status_label || r.status)}</span>
        ${sub}
      </div>
      <div class="carrier-pill"><div class="c-name">${esc(carrierName)}</div></div>
    </div>
    <div><span class="tracking-no">${esc(r.number)}</span>
      <button class="copy-btn" data-copy="${esc(r.number)}" title="Copy">copy</button></div>
    <div class="route">
      <span class="pin">📦 ${esc(locationText(r.origin, "Origin"))}</span>
      <span class="arrow"></span>
      <span class="pin">🏠 ${esc(locationText(r.destination, "Destination"))}</span>
    </div>
    ${chips ? `<div class="eta">${chips}</div>` : ""}
  </div>`;
}

function metric(k, v, isNum = false) {
  const val = isNum
    ? `<div class="v" data-count="${Number(v)}">0</div>`
    : `<div class="v">${esc(v)}</div>`;
  return `<div class="metric"><div class="k">${esc(k)}</div>${val}</div>`;
}

function renderMilestones(milestones) {
  if (!milestones.length) return "";
  const lastReached = milestones.reduce((a, m, i) => (m.reached ? i : a), -1);
  const pct = lastReached <= 0 ? (lastReached === 0 ? 4 : 0) : (lastReached / (milestones.length - 1)) * 100;
  const fillWidth = `calc(${pct}% * 0.78)`;
  const nodes = milestones.map((m, i) => {
    const cls = ["ms", m.reached ? "reached" : "", i === lastReached ? "current" : ""].filter(Boolean).join(" ");
    const tick = m.reached ? "✓" : i + 1;
    const time = m.time_utc ? `<div class="ms-time">${fmtDate(m.time_utc)}</div>` : "";
    return `<div class="${cls}"><div class="ms-node">${tick}</div><div class="ms-label">${esc(m.label)}</div>${time}</div>`;
  }).join("");
  return `<div class="card"><div class="milestones"><div class="ms-fill" data-width="${fillWidth}"></div>${nodes}</div></div>`;
}

function renderTimeline(r) {
  const events = r.events || [];
  const tpl = $("#event-template");
  const items = events.map((ev, i) => {
    const color = META(ev.stage ? stageToStatus(ev.stage) : r.status).color;
    const node = tpl.content.cloneNode(true);
    const li = $(".event", node);
    li.style.setProperty("--i", i);
    li.style.setProperty("--dot", color);
    if (i === 0) li.classList.add("is-latest");
    $(".event-desc", li).textContent = ev.description || "Status update";
    const loc = $(".event-loc", li);
    loc.textContent = ev.location || "";
    const t = ev.time_iso || ev.time_utc;
    $(".event-time", li).textContent = t ? `${fmtAbs(t)} · ${fmtRel(ev.time_utc || ev.time_iso)}` : "";
    const wrap = document.createElement("div");
    wrap.appendChild(node);
    return wrap.innerHTML;
  }).join("");

  const empty = events.length ? "" : `<p style="color:var(--text-faint)">No scan events recorded yet.</p>`;
  const count = events.length ? `<span class="count">${events.length} update${events.length === 1 ? "" : "s"}</span>` : "";
  return `<div class="card">
    <div class="timeline-head"><h2>Tracking history</h2>${count}</div>
    <ul class="timeline">${items}</ul>${empty}
  </div>`;
}

function stageToStatus(stage) {
  if (stage === "Delivered") return "Delivered";
  if (stage === "OutForDelivery") return "OutForDelivery";
  if (stage === "AvailableForPickup") return "AvailableForPickup";
  if (stage === "InfoReceived") return "InfoReceived";
  if (stage === "Exception") return "Exception";
  return "InTransit";
}

/* ── post-render animations ────────────────────────────────────────────── */
function animateMilestoneFill() {
  const fill = $(".ms-fill", resultEl);
  if (fill) fill.style.width = fill.dataset.width || "0";
}

function countUp() {
  resultEl.querySelectorAll("[data-count]").forEach((el) => {
    const target = Number(el.dataset.count) || 0;
    if (REDUCED || target <= 0) { el.textContent = String(target); return; }
    const dur = 900;
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min((now - start) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

function celebrate(color) {
  const palette = [color, "var(--accent)", "var(--accent-2)", "var(--accent-3)", "#fff"];
  const layer = document.createElement("div");
  layer.className = "confetti";
  const N = 36;
  for (let i = 0; i < N; i++) {
    const piece = document.createElement("i");
    piece.style.left = Math.random() * 100 + "vw";
    piece.style.background = palette[i % palette.length];
    piece.style.setProperty("--dur", (2 + Math.random() * 1.8).toFixed(2) + "s");
    piece.style.setProperty("--delay", (Math.random() * 0.5).toFixed(2) + "s");
    piece.style.transform = `rotate(${Math.random() * 360}deg)`;
    layer.appendChild(piece);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 4800);
}

function bindResultEvents() {
  resultEl.querySelectorAll(".copy-btn").forEach((b) =>
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "copied ✓"; }
      catch { b.textContent = "copy failed"; }
      setTimeout(() => (b.textContent = "copy"), 1500);
    })
  );
}

/* ── data fetching ─────────────────────────────────────────────────────── */
async function track(number, carrier) {
  number = (number || "").trim();
  if (!number) return;
  setLoading(true);
  showLoader();
  try {
    const params = new URLSearchParams({ number });
    if (carrier) params.set("carrier", carrier);
    const resp = await fetch(`/api/track?${params.toString()}`);
    const data = await resp.json();
    if (data.ok && data.result) render(data.result);
    else showState(`<span class="state-icon">📭</span>${esc(data.error || "No information found.")}`, true);
  } catch (err) {
    showState(`<span class="state-icon">⚠️</span>Could not reach the tracking service.<br><small>${esc(err.message)}</small>`, true);
  } finally {
    setLoading(false);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  closeCarrierList();
  track(numberInput.value, carrierCode.value);
  updateHash(numberInput.value, carrierCode.value);
});

document.querySelectorAll(".chip").forEach((chip) =>
  chip.addEventListener("click", () => {
    numberInput.value = chip.dataset.num;
    carrierCode.value = chip.dataset.carrier || "";
    carrierInput.value = "";
    track(chip.dataset.num, chip.dataset.carrier);
    updateHash(chip.dataset.num, chip.dataset.carrier);
  })
);

/* ── carrier autocomplete ──────────────────────────────────────────────── */
let carrierTimer = null;
carrierInput.addEventListener("input", () => {
  carrierCode.value = "";
  clearTimeout(carrierTimer);
  const q = carrierInput.value.trim();
  if (q.length < 2) { closeCarrierList(); return; }
  carrierTimer = setTimeout(() => loadCarriers(q), 200);
});
carrierInput.addEventListener("blur", () => setTimeout(closeCarrierList, 150));

async function loadCarriers(q) {
  try {
    const resp = await fetch(`/api/carriers?q=${encodeURIComponent(q)}&limit=8`);
    const { carriers } = await resp.json();
    if (!carriers || !carriers.length) { closeCarrierList(); return; }
    carrierList.innerHTML = carriers.map((c) =>
      `<li role="option" data-code="${c.code}" data-name="${esc(c.name)}">
        <span>${esc(c.name)}</span><span class="c-country">${esc(c.country || "")}</span></li>`).join("");
    carrierList.classList.add("open");
    carrierList.querySelectorAll("li").forEach((li) =>
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        carrierInput.value = li.dataset.name;
        carrierCode.value = li.dataset.code;
        closeCarrierList();
      }));
  } catch { closeCarrierList(); }
}
function closeCarrierList() { carrierList.classList.remove("open"); carrierList.innerHTML = ""; }

/* ── deep linking (supports #nums=…&fc=… and ?number=) ─────────────────── */
function updateHash(number, carrier) {
  const parts = [`nums=${encodeURIComponent(number)}`];
  if (carrier) parts.push(`fc=${encodeURIComponent(carrier)}`);
  history.replaceState(null, "", `#${parts.join("&")}`);
}
function readDeepLink() {
  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  const qs = new URLSearchParams(location.search);
  const number = hash.get("nums") || hash.get("number") || qs.get("number");
  const carrier = hash.get("fc") || hash.get("carrier") || qs.get("carrier");
  if (number) {
    numberInput.value = number;
    if (carrier) carrierCode.value = carrier;
    track(number, carrier);
  }
}

readDeepLink();
