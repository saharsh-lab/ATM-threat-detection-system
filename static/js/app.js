// static/js/app.js — SentinelX application shell.
import { createApi } from "./lib/api.js";
import { h, clear, icon } from "./lib/dom.js";

const api = createApi();
const body = document.body;
const ROLE_RANK = { viewer: 1, analyst: 2, admin: 3 };
const ctx = {
  api,
  mode: body.dataset.mode,
  role: body.dataset.role || "viewer",
  user: body.dataset.user,
  can: (role) => ROLE_RANK[ctx.role] >= ROLE_RANK[role] && ctx.mode !== "demo",
  level: "NORMAL",
  toast,
};

const ROUTES = [
  { id: "overview", title: "Overview", sub: "Security posture across all monitored ATMs", icon: "overview", load: () => import("./views/overview.js") },
  { id: "monitor", title: "Live Monitor", sub: "Annotated camera feeds, tracking and threat scoring", icon: "monitor", load: () => import("./views/monitor.js") },
  { id: "incidents", title: "Incidents", sub: "History, review status and evidence", icon: "incidents", load: () => import("./views/incidents.js") },
  { id: "analytics", title: "Analytics", sub: "Incident trends by severity, rule and camera", icon: "analytics", load: () => import("./views/analytics.js") },
  { id: "models", title: "Model Lab", sub: "Measured detector and pipeline performance", icon: "model", load: () => import("./views/models.js") },
  { id: "datasets", title: "Datasets", sub: "Training and evaluation data, licences and provenance", icon: "datasets", load: () => import("./views/datasets.js") },
  { id: "settings", title: "Settings", sub: "Thresholds, audit log and backups", icon: "settings", load: () => import("./views/settings.js") },
];

// ── theme ────────────────────────────────────────────────────────────────
function storedTheme() { try { return localStorage.getItem("sx-theme"); } catch { return null; } }
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  const dark = (t || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")) === "dark";
  const btn = document.getElementById("theme-btn");
  clear(btn).append(icon(dark ? "sun" : "moon"));
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
applyTheme(storedTheme());
document.getElementById("theme-btn").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  const next = cur === "dark" ? "light" : "dark";
  try { localStorage.setItem("sx-theme", next); } catch { /* storage unavailable */ }
  applyTheme(next);
  route();   // re-render so charts pick up the new theme tokens
});

// ── chrome ───────────────────────────────────────────────────────────────
const nav = document.getElementById("nav");
nav.append(h("div", { class: "nav-label", text: "Operate" }));
ROUTES.forEach((r, i) => {
  if (i === 4) nav.append(h("div", { class: "nav-label", text: "Research" }));
  if (i === 6) nav.append(h("div", { class: "nav-label", text: "Admin" }));
  nav.append(h("a", { href: `#/${r.id}`, "data-route": r.id }, icon(r.icon), h("span", { text: r.title }),
    r.id === "incidents" ? h("span", { class: "count", id: "open-count", hidden: true }) : null));
});
const menuBtn = document.getElementById("menu-btn");
menuBtn.append(icon("menu"));
const sidebar = document.getElementById("sidebar");
menuBtn.addEventListener("click", () => {
  const open = sidebar.dataset.open !== "true";
  sidebar.dataset.open = String(open);
  menuBtn.setAttribute("aria-expanded", String(open));
});
nav.addEventListener("click", () => { sidebar.dataset.open = "false"; menuBtn.setAttribute("aria-expanded", "false"); });

const logoutBtn = document.getElementById("logout-btn");
if (logoutBtn) {
  logoutBtn.append(icon("logout"));
  logoutBtn.addEventListener("click", async () => { try { await api.logout(); } finally { location.href = "/login"; } });
}
const foot = document.getElementById("foot-user");
if (foot && ctx.user) foot.textContent = `${ctx.user} · ${ctx.role}`;

const clock = document.getElementById("clock");
const tick = () => { clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
tick(); setInterval(tick, 1000);

// ── toasts & banner ──────────────────────────────────────────────────────
function toast(message, level = "info", { timeout = 7000 } = {}) {
  const el = h("div", { class: "toast", "data-level": level, role: level === "error" ? "alert" : "status" },
    h("div", {}, h("div", { text: message })),
    h("button", { class: "x", "aria-label": "Dismiss", text: "×", onclick: () => el.remove() }));
  document.getElementById("toasts").append(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
}

const bannerSlot = document.getElementById("banner-slot");
function showBanner(alert) {
  clear(bannerSlot);
  if (!alert) return;
  bannerSlot.append(h("div", { class: "banner", role: "alert" },
    icon("alert"), h("b", { text: alert.severity }), h("span", { text: alert.message }),
    h("span", { class: "muted mono", text: new Date(alert.ts).toLocaleTimeString() }),
    h("button", { class: "btn small", text: "Review incident", onclick: () => {
      location.hash = `#/incidents?id=${encodeURIComponent(alert.incident_id || "")}`; clear(bannerSlot); } }),
    h("button", { class: "btn small ghost", text: "Dismiss", onclick: () => clear(bannerSlot) })));
}

// ── alert polling (live mode) ────────────────────────────────────────────
let lastAlertId = null;
async function pollAlerts() {
  if (ctx.mode === "demo") return;
  try {
    const { alerts } = await api.alerts(lastAlertId ?? 0);
    if (lastAlertId === null) {           // first poll: establish baseline, don't replay history
      lastAlertId = alerts.length ? Math.max(...alerts.map((a) => a.id)) : 0;
    } else if (alerts.length) {
      lastAlertId = Math.max(lastAlertId, ...alerts.map((a) => a.id));
      const newest = alerts[0];
      alerts.slice(0, 3).reverse().forEach((a) => toast(a.message, a.severity));
      if (newest.severity === "CRITICAL" || newest.severity === "HIGH") showBanner(newest);
    }
    const stats = await api.incidents({ status: "open", per_page: 1 });
    const badge = document.getElementById("open-count");
    badge.hidden = !stats.total;
    badge.textContent = stats.total;
  } catch (e) {
    if (e.status !== 401) console.warn("alert poll failed", e.message);
  }
}
pollAlerts();
setInterval(pollAlerts, 4000);

// ── router ───────────────────────────────────────────────────────────────
const content = document.getElementById("content");
let current = null;

function parseHash() {
  const [path, query] = (location.hash.replace(/^#\/?/, "") || "overview").split("?");
  return { id: path || "overview", params: new URLSearchParams(query || "") };
}

async function route() {
  const { id, params } = parseHash();
  const r = ROUTES.find((x) => x.id === id) || ROUTES[0];
  if (current?.mod?.destroy) current.mod.destroy();
  nav.querySelectorAll("a").forEach((a) => {
    if (a.dataset.route === r.id) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.getElementById("view-title").textContent = r.title;
  document.getElementById("view-sub").textContent = r.sub;
  document.title = `${r.title} · SentinelX`;
  const view = h("div", { class: "view" });
  clear(content).append(view);
  try {
    const mod = await r.load();
    current = { id: r.id, mod };
    await mod.render(view, { ...ctx, params });
  } catch (e) {
    console.error(e);
    view.append(h("div", { class: "card" }, h("h2", { text: "This view could not be loaded" }),
      h("p", { class: "muted", text: e.message || String(e) })));
  }
  content.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);
route();
