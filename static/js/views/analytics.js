// Analytics — incident trends. One range control scopes every chart.
import { h, fmt } from "../lib/dom.js";
import { barChart, columnChart, stackBar, sevColor } from "../lib/charts.js";
import { RULE_LABELS } from "./overview.js";

const RANGES = [["24 hours", 24], ["7 days", 168], ["30 days", 720], ["90 days", 2160]];
let hours = 168;

export async function render(root, ctx) {
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Date range" });
  RANGES.forEach(([label, hr]) => seg.append(h("button", { "aria-pressed": String(hr === hours), text: label,
    onclick: (e) => { hours = hr; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.target))); load(); } })));
  root.append(h("div", { class: "filters" }, h("div", { class: "field" }, "Range", seg)));
  if (ctx.mode === "demo") root.append(h("div", { class: "note warn", text: "Demo data: counts come from precomputed detections on research videos, not from live operation." }));

  const cards = {};
  const card = (key, title, sub, span) => {
    cards[key] = h("div", { class: "chart" });
    return h("section", { class: `card${span ? " span-2" : ""}` }, h("div", { class: "card-head" }, h("h2", { text: title }), h("span", { class: "sub", text: sub })), cards[key]);
  };
  const totals = h("div", { class: "grid g-4" });
  root.append(totals, h("div", { class: "grid g-2" },
    card("timeline", "Incidents over time", "count per period", true),
    card("severity", "Severity", "share of incidents"), card("status", "Review status", "analyst workflow"),
    card("rules", "Triggering rules", "incidents in which each rule fired"), card("cameras", "By camera", "incident count"),
    card("source", "By source", "live vs recorded vs demo")));

  async function load() {
    Object.values(cards).forEach((c) => c.classList.add("loading"));
    try {
      const s = await ctx.api.stats(hours);
      totals.replaceChildren(...[["Incidents", s.total], ["Open", s.open], ["Mean peak score", (s.avg_peak_score || 0).toFixed(0)],
        ["False-positive reviews", s.by_status.false_positive || 0]].map(([l, v]) =>
        h("div", { class: "card stat" }, h("div", { class: "label", text: l }), h("div", { class: "value", text: String(v) }))));

      // bucket hourly → daily when the range is long
      const daily = hours > 72;
      const buckets = {};
      for (const r of s.hourly || []) {
        const k = daily ? r.hour.slice(0, 10) : r.hour;
        buckets[k] = (buckets[k] || 0) + r.n;
      }
      const keys = [];
      const step = daily ? 24 : 1;
      for (let i = Math.floor(hours / step) - 1; i >= 0; i--) {
        const d = new Date(Date.now() - i * step * 3600e3).toISOString();
        keys.push(daily ? d.slice(0, 10) : d.slice(0, 13));
      }
      if (ctx.mode === "demo") cards.timeline.replaceChildren(h("p", { class: "empty", text: "Demo incidents carry media time only." }));
      else columnChart(cards.timeline, { categories: keys.map((k) => (daily ? k.slice(5) : `${k.slice(11)}:00`)),
        series: [{ name: "Incidents", color: css("--series-1"), values: keys.map((k) => buckets[k] || 0) }],
        caption: "Incidents over time", height: 220, integer: true });
      stackBar(cards.severity, { parts: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((l) => ({ label: l, value: s.by_severity[l] || 0, color: sevColor(l) })), caption: "Severity" });
      const toItems = (obj, label = (k) => k) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: label(k), value: v }));
      const show = (key, items, color) => (items.length ? barChart(cards[key], { items, color, caption: key })
        : cards[key].replaceChildren(h("p", { class: "empty", text: "No data in this range." })));
      show("status", toItems(s.by_status, fmt.label), css("--series-2"));
      show("rules", toItems(s.by_rule, (k) => RULE_LABELS[k] || k).slice(0, 10), css("--series-2"));
      show("cameras", toItems(s.by_camera), css("--series-1"));
      show("source", toItems(s.by_source, fmt.label), css("--series-3"));
    } catch (e) {
      ctx.toast(`Analytics failed: ${e.message}`, "error");
    } finally { Object.values(cards).forEach((c) => c.classList.remove("loading")); }
  }
  await load();
}
