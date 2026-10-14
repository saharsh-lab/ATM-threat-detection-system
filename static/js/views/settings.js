// Settings — runtime thresholds (admin), audit log, backups.
import { h, clear, fmt } from "../lib/dom.js";

export async function render(root, ctx) {
  if (ctx.mode === "demo") {
    root.append(h("div", { class: "card empty", text: "Settings, audit log and backups are available when SentinelX runs locally." }));
    return;
  }
  const admin = ctx.can("admin");
  const { settings } = await ctx.api.settings();
  const inputs = {};
  const form = h("form", { class: "grid g-2", style: { gap: "14px" }, onsubmit: async (e) => {
    e.preventDefault();
    const values = {};
    for (const s of settings) {
      const el = inputs[s.key];
      values[s.key] = s.type === "bool" ? el.checked : s.type === "str" ? el.value : Number(el.value);
    }
    try { await ctx.api.saveSettings(values); ctx.toast("Settings saved and audited."); }
    catch (err) { ctx.toast(err.message, "error"); }
  } });
  for (const s of settings) {
    let el;
    if (s.type === "bool") el = h("input", { type: "checkbox", checked: s.value, disabled: !admin });
    else if (s.choices) el = h("select", { class: "input", disabled: !admin }, s.choices.map((c) => h("option", { value: c, text: c, selected: c === s.value })));
    else el = h("input", { class: "input", type: "number", value: s.value, min: s.min, max: s.max, step: s.type === "int" ? 1 : "any", required: true, disabled: !admin });
    inputs[s.key] = el;
    form.append(h("label", { class: "field" }, h("span", {}, h("b", { style: { color: "var(--text)" }, text: fmt.label(s.key) }), ` — ${s.description}`), el,
      h("span", { class: "muted", text: `default ${s.default}${s.min !== undefined ? ` · range ${s.min}–${s.max}` : ""}` })));
  }
  if (admin) form.append(h("div", {}, h("button", { class: "btn primary", type: "submit", text: "Save thresholds" })));
  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Detection & alert thresholds" }),
    h("span", { class: "sub", text: admin ? "changes apply immediately and are recorded in the audit log" : "read-only — admin role required" })), form));

  if (!admin) return;
  const auditEl = h("div");
  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Audit log" }),
    h("div", { class: "actions" }, h("button", { class: "btn small", text: "Back up database now", onclick: async () => {
      try { const r = await ctx.api.backup(); ctx.toast(`Backup written: ${r.file}`); loadAudit(); } catch (e) { ctx.toast(e.message, "error"); } } }))), auditEl));
  async function loadAudit() {
    const { items } = await ctx.api.audit({ limit: 100 });
    clear(auditEl).append(h("div", { class: "table-wrap", style: { maxHeight: "460px" } }, h("table", {},
      h("thead", {}, h("tr", {}, ["Time", "Actor", "Action", "Entity", "Details", "IP"].map((x) => h("th", { text: x })))),
      h("tbody", {}, items.map((a) => h("tr", {}, h("td", { text: fmt.datetime(a.ts) }), h("td", { text: a.actor }), h("td", { class: "mono", text: a.action }),
        h("td", { class: "mono", text: [a.entity_type, a.entity_id].filter(Boolean).join(":") }),
        h("td", { class: "mono wrap", text: JSON.stringify(a.details) }), h("td", { class: "mono", text: a.ip || "" })))))));
  }
  loadAudit();
}
