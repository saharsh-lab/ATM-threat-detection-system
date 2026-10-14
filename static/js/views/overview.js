// Overview — posture hero, KPIs, trends, camera status.
import { h, icon, fmt, countTo, srcBadge, sevBadge } from "../lib/dom.js";
import { columnChart, stackBar, barChart, sevColor } from "../lib/charts.js";
import { mountWireframe } from "../lib/wireframe.js";

export const RULE_LABELS = {
  multiple_persons: "Multiple persons", close_proximity: "Close proximity", loitering: "Loitering",
  vandalism: "Tampering posture", rapid_motion: "Rapid motion", weapon: "Bladed weapon", firearm: "Firearm",
  blunt_tool: "Blunt tool", face_concealment: "Face concealment", threat_object: "Suspicious object",
  unattended_bag: "Unattended bag", restricted_zone: "Restricted zone", camera_tamper: "Camera tampering",
  learned_anomaly: "Learned temporal anomaly",
};
const LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
let unmount = null, timer = null, level = "NORMAL";

function tile(label, iconName, valueEl, foot) {
  return h("div", { class: "card stat" },
    h("div", { class: "label" }, h("span", { class: "icon" }, icon(iconName)), label), valueEl,
    h("div", { class: "foot", text: foot }));
}

export async function render(root, ctx) {
  const demo = ctx.mode === "demo";
  const openEl = h("b", { text: "0" }), camsEl = h("b", { text: "—" }), dayEl = h("b", { text: "—" });
  const canvas = h("canvas", { "aria-hidden": "true" });
  root.append(h("section", { class: "card hero", "aria-labelledby": "posture-h" },
    h("div", { class: "hero-copy" },
      h("span", { class: "eyebrow", text: demo ? "Demonstration · precomputed data" : "Security posture" }),
      h("h2", { id: "posture-h", text: demo
        ? "Replay of SentinelX detections on ATMA-V research videos."
        : "Every monitored vestibule, scored continuously and logged to an auditable incident trail." }),
      h("div", { class: "posture" },
        h("div", {}, openEl, h("span", { text: demo ? "demo incidents" : "open incidents" })),
        h("div", {}, camsEl, h("span", { text: "feeds streaming" })),
        h("div", {}, dayEl, h("span", { text: demo ? "incidents in demo set" : "incidents, last 24 h" }))),
      h("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap" } },
        h("a", { class: "btn primary", href: "#/monitor" }, icon("monitor"), "Open live monitor"),
        h("a", { class: "btn", href: "#/incidents" }, "Review incidents"))),
    h("div", { class: "hero-viz" }, canvas, h("span", { class: "viz-caption", text: "Kiosk model · camera frustum · scan plane" }))));
  unmount = mountWireframe(canvas, { getLevel: () => level });

  const kpi = {
    total: h("div", { class: "value", text: "—" }), severe: h("div", { class: "value", text: "—" }),
    peak: h("div", { class: "value", text: "—" }), fpr: h("div", { class: "value", text: "—" }),
  };
  root.append(h("div", { class: "grid g-4" },
    tile("Incidents, last 7 days", "incidents", kpi.total, demo ? "Demo set" : "All sources"),
    tile("High & critical", "alert", kpi.severe, "Severity ≥ HIGH"),
    tile("Mean peak threat score", "target", kpi.peak, "0–100 scale"),
    tile("Reviewed as false positive", "shield", kpi.fpr, "Share of reviewed incidents")));

  const hourly = h("div"), sev = h("div"), rules = h("div"), cams = h("div");
  root.append(h("div", { class: "grid g-main" },
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Incidents per hour" }),
      h("span", { class: "sub", text: "last 24 hours" })), hourly),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Severity mix" }),
      h("span", { class: "sub", text: "last 7 days" })), sev,
      h("div", { class: "card-head", style: { marginTop: "22px" } }, h("h2", { text: "Top triggering rules" })), rules)));
  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Camera status" }),
    h("a", { class: "btn small actions", href: "#/monitor", text: "Monitor" })), cams));

  async function refresh() {
    try {
      const [week, day, camsRes] = await Promise.all([ctx.api.stats(168), ctx.api.stats(24), ctx.api.cameras()]);
      countTo(openEl, demo ? week.total : (week.by_status.open || 0));
      countTo(dayEl, day.total);
      const streaming = camsRes.cameras.filter((c) => ["live", "replaying", "demo"].includes(c.status)).length;
      camsEl.textContent = `${streaming}/${camsRes.cameras.length}`;
      countTo(kpi.total, week.total);
      countTo(kpi.severe, (week.by_severity.HIGH || 0) + (week.by_severity.CRITICAL || 0));
      countTo(kpi.peak, week.avg_peak_score || 0, { format: (v) => v.toFixed(0) });
      const reviewed = Object.entries(week.by_status).filter(([k]) => k !== "open").reduce((a, [, v]) => a + v, 0);
      kpi.fpr.textContent = reviewed ? fmt.pct((week.by_status.false_positive || 0) / reviewed, 0) : "—";

      const now = new Date();
      const hours = [...Array(24)].map((_, i) => new Date(now.getTime() - (23 - i) * 3600e3));
      const key = (d) => d.toISOString().slice(0, 13);
      const counts = Object.fromEntries((day.hourly || []).map((x) => [x.hour, x.n]));
      if (demo) hourly.replaceChildren(h("p", { class: "empty", text: "Demo incidents have no wall-clock time — see Incidents for media timestamps." }));
      else columnChart(hourly, { categories: hours.map((d) => `${String(d.getHours()).padStart(2, "0")}:00`),
        series: [{ name: "Incidents", color: getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim(),
          values: hours.map((d) => counts[key(d)] || 0) }], caption: "Incidents per hour, last 24 hours", height: 210, integer: true });
      stackBar(sev, { parts: LEVELS.map((l) => ({ label: l, value: week.by_severity[l] || 0, color: sevColor(l) })),
        caption: "Incidents by severity" });
      const ruleItems = Object.entries(week.by_rule || {}).sort((a, b) => b[1] - a[1]).slice(0, 6)
        .map(([k, v]) => ({ label: RULE_LABELS[k] || k, value: v }));
      if (ruleItems.length) barChart(rules, { items: ruleItems, color: getComputedStyle(document.documentElement).getPropertyValue("--series-2").trim(), caption: "Top triggering rules" });
      else rules.replaceChildren(h("p", { class: "empty", text: "No rule activations in this period." }));

      level = "NORMAL";
      cams.replaceChildren(h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Camera", "Source", "Status", "Threat", "Score", "Model"].map((x) => h("th", { text: x })))),
        h("tbody", {}, camsRes.cameras.map((c) => {
          const lvl = c.threat?.level || "NORMAL";
          if (["HIGH", "CRITICAL"].includes(lvl) || (lvl === "MEDIUM" && level === "NORMAL")) level = lvl;
          return h("tr", { "data-clickable": "", onclick: () => { location.hash = `#/monitor?camera=${encodeURIComponent(c.camera_id)}`; } },
            h("td", {}, h("b", { text: c.name }), h("div", { class: "muted", style: { fontSize: "11.5px" }, text: c.location || c.camera_id })),
            h("td", {}, srcBadge(c.source_kind || (c.source_type === "file" ? "recorded" : "live"))),
            h("td", {}, h("span", { class: "status-dot", "data-s": c.status }), " ", fmt.label(c.status),
              c.error ? h("div", { class: "muted", style: { fontSize: "11px" }, text: c.error }) : null),
            h("td", {}, demo ? "—" : sevBadge(lvl)),
            h("td", { class: "num", text: demo ? "—" : String(c.threat?.score ?? 0) }),
            h("td", { class: "mono", text: c.model?.id || (demo ? "precomputed" : "loading…") }));
        })))));
    } catch (e) {
      ctx.toast(`Overview refresh failed: ${e.message}`, "error");
    }
  }
  await refresh();
  timer = setInterval(refresh, demo ? 60000 : 5000);
}

export function destroy() { unmount?.(); clearInterval(timer); }
