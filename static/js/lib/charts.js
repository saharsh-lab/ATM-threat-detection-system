// static/js/lib/charts.js — dependency-free SVG charts.
// Specs (dataviz method): bars ≤ 24px with 4px rounded data-end, square at the
// baseline; 2px lines; hairline solid gridlines; text in ink tokens, never in
// series colour; legend for ≥ 2 series; hover/focus tooltips; every chart has a
// table view so no value is gated behind hover.

import { h, svg, clear } from "./dom.js";

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
let tip;

function tooltip() {
  if (!tip) { tip = h("div", { class: "tooltip", role: "status", hidden: true }); document.body.append(tip); }
  return tip;
}

function showTip(evt, title, rows) {
  const t = tooltip();
  clear(t);
  t.append(h("div", { class: "t-title", text: title }));
  for (const r of rows) {
    t.append(h("div", { class: "t-row" },
      r.color ? h("span", { class: "t-key", style: { background: r.color } }) : null,
      h("b", { text: r.value }), h("span", { class: "muted", text: r.label || "" })));
  }
  t.hidden = false;
  const rect = evt.target.getBoundingClientRect?.() || { left: evt.clientX, top: evt.clientY, width: 0 };
  const x = evt.clientX ?? rect.left + rect.width / 2;
  const y = evt.clientY ?? rect.top;
  const w = t.offsetWidth, hgt = t.offsetHeight;
  t.style.left = `${Math.min(window.innerWidth - w - 8, Math.max(8, x + 12))}px`;
  t.style.top = `${Math.max(8, y - hgt - 12)}px`;
}
const hideTip = () => { if (tip) tip.hidden = true; };

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
const compact = (n) => (Math.abs(n) >= 1000 ? `${+(n / 1000).toFixed(1)}K` : `${+n.toFixed(2)}`);

/** Rounded-top bar path: 4px radius at the data end, square at the baseline. */
function barPath(x, y, w, hgt, horizontal = false) {
  const r = Math.min(4, (horizontal ? hgt : w) / 2, horizontal ? w : hgt);
  if (hgt <= 0 || w <= 0) return "";
  if (horizontal) return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${hgt - 2 * r}a${r},${r} 0 0 1 -${r},${r}h-${w - r}z`;
  return `M${x},${y + hgt}v-${hgt - r}a${r},${r} 0 0 1 ${r},-${r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${hgt - r}z`;
}

function legend(series) {
  if (series.length < 2) return null;
  return h("div", { class: "legend", role: "list" }, series.map((s) =>
    h("span", { role: "listitem" }, h("i", { class: s.kind === "line" ? "line" : "", style: { background: s.color } }), s.name)));
}

function tableView(caption, headers, rows) {
  const wrap = h("details", { class: "chart-table" },
    h("summary", { class: "muted", style: { cursor: "pointer", fontSize: "12px" }, text: "Show data table" }),
    h("div", { class: "table-wrap" }, h("table", {},
      h("caption", { class: "sr-only", text: caption }),
      h("thead", {}, h("tr", {}, headers.map((x, i) => h("th", { class: i ? "num" : "", text: x })))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i ? "num" : "", text: c ?? "—" }))))))));
  return wrap;
}

/**
 * Vertical (grouped) column chart.
 * categories: [label], series: [{name, color, values:[number|null]}]
 */
export function columnChart(el, { categories, series, height = 220, yLabel = "", format = compact, max = null, caption = "", integer = false }) {
  clear(el);
  el.classList.add("chart");
  const W = Math.max(320, el.clientWidth || 600), H = height, m = { t: 14, r: 8, b: 36, l: 40 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  let vmax = max ?? niceMax(Math.max(0, ...series.flatMap((s) => s.values.filter((v) => v !== null))));
  if (integer) vmax = Math.max(4, Math.ceil(vmax / 4) * 4);     // whole-number ticks for counts
  const band = iw / Math.max(categories.length, 1);
  const labelEvery = Math.max(1, Math.ceil((categories.length * 44) / iw));   // thin x labels to avoid overlap
  const gap = 2;
  const barW = Math.min(24, (band * 0.7 - gap * (series.length - 1)) / series.length);
  const groupW = barW * series.length + gap * (series.length - 1);
  const y = (v) => m.t + ih - (v / vmax) * ih;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": caption || yLabel });
  for (let i = 0; i <= 4; i++) {
    const v = (vmax / 4) * i;
    root.append(svg("line", { class: i ? "gridline" : "axis-line", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }),
      svg("text", { class: "tick", x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: format(v) }));
  }
  const showLabels = categories.length * series.length <= 18;
  categories.forEach((cat, ci) => {
    const gx = m.l + band * ci + (band - groupW) / 2;
    series.forEach((s, si) => {
      const v = s.values[ci];
      const x = gx + si * (barW + gap);
      if (v === null || v === undefined) {
        root.append(svg("text", { class: "tick", x: x + barW / 2, y: y(0) - 4, "text-anchor": "middle", text: "n/a" }));
        return;
      }
      const top = y(v), bh = y(0) - top;
      const hit = svg("rect", { class: "hit", x: x - 3, y: m.t, width: barW + 6, height: ih, tabindex: 0,
        "aria-label": `${cat}, ${s.name}: ${format(v)}` });
      const mark = svg("path", { class: "mark", d: barPath(x, top, barW, Math.max(bh, 0.5)), fill: s.color });
      const enter = (e) => showTip(e, cat, series.map((ss) => ({ color: ss.color, label: ss.name,
        value: ss.values[ci] === null || ss.values[ci] === undefined ? "n/a" : format(ss.values[ci]) })));
      hit.addEventListener("pointermove", enter); hit.addEventListener("focus", enter);
      hit.addEventListener("pointerleave", hideTip); hit.addEventListener("blur", hideTip);
      root.append(hit, mark);
      if (showLabels && bh > 0) root.append(svg("text", { class: "dlabel", x: x + barW / 2, y: top - 5, "text-anchor": "middle", text: format(v) }));
    });
    const lbl = cat.length > 12 && categories.length > 6 ? `${cat.slice(0, 11)}…` : cat;
    if (ci % labelEvery === 0) root.append(svg("text", { class: "tick", x: m.l + band * ci + band / 2, y: H - m.b + 16, "text-anchor": "middle", text: lbl }));
  });
  el.append(legend(series) || "", root,
    tableView(caption, ["Category", ...series.map((s) => s.name)], categories.map((c, i) => [c, ...series.map((s) => (s.values[i] === null || s.values[i] === undefined ? "n/a" : format(s.values[i])))])));
}

/** Horizontal bar chart for ranked categories (one series). */
export function barChart(el, { items, color, height = null, format = compact, caption = "" }) {
  clear(el);
  el.classList.add("chart");
  const rowH = 30;
  const W = Math.max(300, el.clientWidth || 500), H = height || Math.max(60, items.length * rowH + 10);
  const labelW = Math.min(170, W * 0.38), m = { l: labelW, r: 46, t: 4 };
  const iw = W - m.l - m.r;
  const vmax = niceMax(Math.max(1, ...items.map((i) => i.value)));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": caption });
  root.append(svg("line", { class: "axis-line", x1: m.l, x2: m.l, y1: 0, y2: H }));
  items.forEach((it, i) => {
    const yy = m.t + i * rowH + (rowH - 14) / 2;
    const w = (it.value / vmax) * iw;
    const fill = it.color || color;
    const hit = svg("rect", { class: "hit", x: 0, y: m.t + i * rowH, width: W, height: rowH, tabindex: 0,
      "aria-label": `${it.label}: ${format(it.value)}` });
    const enter = (e) => showTip(e, it.label, [{ color: fill, value: format(it.value), label: it.note || "" }]);
    hit.addEventListener("pointermove", enter); hit.addEventListener("focus", enter);
    hit.addEventListener("pointerleave", hideTip); hit.addEventListener("blur", hideTip);
    root.append(hit,
      svg("text", { class: "tick", x: m.l - 8, y: yy + 11, "text-anchor": "end", text: it.label.length > 24 ? `${it.label.slice(0, 23)}…` : it.label }),
      svg("path", { class: "mark", d: barPath(m.l, yy, Math.max(w, 0.5), 14, true), fill }),
      svg("text", { class: "dlabel", x: m.l + w + 6, y: yy + 11, text: format(it.value) }));
  });
  el.append(root, tableView(caption, ["Category", "Value"], items.map((i) => [i.label, format(i.value)])));
}

/** Line / area chart with crosshair tooltip. xs: numbers or Date; series: [{name,color,values}] */
export function lineChart(el, { xs, series, height = 180, yMax = null, xFormat = (x) => x, format = compact,
  area = true, caption = "", bands = [] }) {
  clear(el);
  el.classList.add("chart");
  const W = Math.max(300, el.clientWidth || 600), H = height, m = { t: 10, r: 10, b: 24, l: 34 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = xs.length;
  const vmax = yMax ?? niceMax(Math.max(1, ...series.flatMap((s) => s.values)));
  const x = (i) => m.l + (n <= 1 ? iw : (i / (n - 1)) * iw);
  const y = (v) => m.t + ih - (Math.min(v, vmax) / vmax) * ih;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": caption });
  for (const b of bands) {   // shaded reference bands (e.g. ground-truth anomaly)
    root.append(svg("rect", { x: x(b.from), y: m.t, width: Math.max(1, x(b.to) - x(b.from)), height: ih, fill: b.color, opacity: 0.12 }));
  }
  for (let i = 0; i <= 4; i++) {
    const v = (vmax / 4) * i;
    root.append(svg("line", { class: i ? "gridline" : "axis-line", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }),
      svg("text", { class: "tick", x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: format(v) }));
  }
  const ticks = Math.min(5, n);
  for (let k = 0; k < ticks; k++) {
    const i = Math.round((k / Math.max(ticks - 1, 1)) * (n - 1));
    root.append(svg("text", { class: "tick", x: x(i), y: H - 6, "text-anchor": k === 0 ? "start" : k === ticks - 1 ? "end" : "middle", text: n ? xFormat(xs[i]) : "" }));
  }
  for (const s of series) {
    if (!n) continue;
    const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    if (area) root.append(svg("polygon", { points: `${x(0)},${y(0)} ${pts} ${x(n - 1)},${y(0)}`, fill: s.color, opacity: 0.1 }));
    root.append(svg("polyline", { points: pts, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = s.values[n - 1];
    root.append(svg("circle", { cx: x(n - 1), cy: y(last), r: 4, fill: s.color, stroke: css("--surface"), "stroke-width": 2 }));
  }
  const cross = svg("line", { class: "crosshair", y1: m.t, y2: m.t + ih, x1: 0, x2: 0, visibility: "hidden" });
  const overlay = svg("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", tabindex: 0, "aria-label": `${caption} — use arrow keys` });
  let idx = n - 1;
  const show = (e, i) => {
    idx = Math.max(0, Math.min(n - 1, i));
    cross.setAttribute("x1", x(idx)); cross.setAttribute("x2", x(idx)); cross.setAttribute("visibility", "visible");
    showTip(e, String(xFormat(xs[idx])), series.map((s) => ({ color: s.color, label: s.name, value: format(s.values[idx]) })));
  };
  overlay.addEventListener("pointermove", (e) => {
    const r = root.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    show(e, Math.round(((px - m.l) / iw) * (n - 1)));
  });
  overlay.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const r = overlay.getBoundingClientRect();
      show({ clientX: r.left + ((x(idx) - m.l) / iw) * r.width, clientY: r.top }, idx + (e.key === "ArrowRight" ? 1 : -1));
    }
  });
  overlay.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  overlay.addEventListener("blur", hideTip);
  root.append(cross, overlay);
  el.append(legend(series.map((s) => ({ ...s, kind: "line" }))) || "", root);
  if (caption) el.append(tableView(caption, ["X", ...series.map((s) => s.name)], xs.map((xx, i) => [String(xFormat(xx)), ...series.map((s) => format(s.values[i]))])));
}

/** Stacked single bar (proportions) with 2px surface gaps and an inline legend. */
export function stackBar(el, { parts, caption = "" }) {
  clear(el);
  el.classList.add("chart");
  const total = parts.reduce((a, p) => a + p.value, 0);
  const W = Math.max(280, el.clientWidth || 400), H = 16;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": caption });
  let x = 0;
  const live = parts.filter((p) => p.value > 0);
  live.forEach((p, i) => {
    const w = total ? (p.value / total) * (W - 2 * (live.length - 1)) : 0;
    const r = 4;
    const first = i === 0, last = i === live.length - 1;
    const d = `M${x + (first ? r : 0)},0h${w - (first ? r : 0) - (last ? r : 0)}${last ? `a${r},${r} 0 0 1 ${r},${r}v${H - 2 * r}a${r},${r} 0 0 1 -${r},${r}` : `v${H}`}h-${w - (first ? r : 0) - (last ? r : 0)}${first ? `a${r},${r} 0 0 1 -${r},-${r}v-${H - 2 * r}a${r},${r} 0 0 1 ${r},-${r}` : `v-${H}`}z`;
    const seg = svg("path", { class: "mark", d, fill: p.color, tabindex: 0, "aria-label": `${p.label}: ${p.value}` });
    const enter = (e) => showTip(e, p.label, [{ color: p.color, value: `${p.value}`, label: total ? `${Math.round((p.value / total) * 100)}%` : "" }]);
    seg.addEventListener("pointermove", enter); seg.addEventListener("focus", enter);
    seg.addEventListener("pointerleave", hideTip); seg.addEventListener("blur", hideTip);
    root.append(seg);
    x += w + 2;
  });
  if (!total) root.append(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 4, fill: css("--surface-3") }));
  el.append(root, h("div", { class: "legend", style: { marginTop: "10px" } },
    parts.map((p) => h("span", {}, h("i", { style: { background: p.color } }), `${p.label} `, h("b", { text: String(p.value) })))));
}

export const seriesColors = () => [css("--series-1"), css("--series-2"), css("--series-3")];
export const sevColor = (lvl) => css(`--sev-${String(lvl).toLowerCase()}`);
