// static/js/lib/dom.js — tiny DOM helpers. All text goes through textContent:
// incident reasons, camera names and notes are untrusted data.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElementNS(
    tag.startsWith("svg:") ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml",
    tag.replace(/^svg:/, ""));
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const svg = (tag, attrs, ...children) => h(`svg:${tag}`, attrs, ...children);

export function clear(el) { while (el.firstChild) el.firstChild.remove(); return el; }

export function icon(name) {
  const paths = {
    overview: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z",
    monitor: "M3 5h18v12H3zM8 21h8M12 17v4",
    incidents: "M12 3l9 16H3zM12 10v4M12 17h.01",
    analytics: "M4 20V10M10 20V4M16 20v-7M22 20H2",
    model: "M12 2l9 5v10l-9 5-9-5V7zM12 12l9-5M12 12v10M12 12L3 7",
    datasets: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
    settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
    sun: "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
    moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
    menu: "M3 6h18M3 12h18M3 18h18",
    logout: "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",
    shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
    camera: "M23 7l-7 5 7 5zM1 5h15v14H1z",
    clock: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2",
    alert: "M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01",
    download: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
    upload: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12",
    target: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 18a6 6 0 100-12 6 6 0 000 12zM12 14a2 2 0 100-4 2 2 0 000 4z",
  };
  return svg("svg", { class: "ico", viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: paths[name] || "" }));
}

export const fmt = {
  int: (n) => (n === null || n === undefined ? "—" : Math.round(n).toLocaleString()),
  compact: (n) => (n === null || n === undefined ? "—" :
    Math.abs(n) >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : Math.abs(n) >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : `${n}`),
  pct: (x, d = 1) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(d)}%`),
  num: (x, d = 3) => (x === null || x === undefined ? "—" : Number(x).toFixed(d)),
  time: (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"),
  datetime: (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"),
  duration: (a, b) => {
    if (!a) return "—";
    const s = Math.max(0, ((b ? new Date(b) : new Date()) - new Date(a)) / 1000);
    return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${(s / 3600).toFixed(1)}h`;
  },
  label: (s) => String(s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()),
};

export function sevBadge(level) {
  return h("span", { class: "sev", "data-level": level || "NORMAL", text: level || "NORMAL" });
}

export function srcBadge(kind) {
  const label = { live: "Live", recorded: "Recorded", demo: "Demo data" }[kind] || kind;
  return h("span", { class: "src", "data-src": kind, title: {
    live: "Detections from a live camera stream",
    recorded: "Detections from a recorded video replayed through the live pipeline",
    demo: "Precomputed demonstration data — not a live detection" }[kind] || "", text: label });
}

/** Animate a number from its current value to `to` (respects reduced motion). */
export function countTo(el, to, { duration = 700, format = fmt.int } = {}) {
  const from = Number(el.dataset.v || 0);
  el.dataset.v = to;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || from === to) { el.textContent = format(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / duration);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = format(from + (to - from) * e);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
