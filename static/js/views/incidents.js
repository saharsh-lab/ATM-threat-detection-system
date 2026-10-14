// Incidents — filterable history, review workflow, evidence and audit trail.
import { h, clear, icon, fmt, sevBadge, srcBadge } from "../lib/dom.js";
import { RULE_LABELS } from "./overview.js";

const STATUSES = ["open", "acknowledged", "investigating", "resolved", "false_positive"];
const SEVS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
const RANGES = [["24h", 24], ["7 days", 168], ["30 days", 720], ["All", 0]];
let state = null;

export async function render(root, ctx) {
  state = { page: 1, per_page: 20, sort: "started_at", order: "desc", hours: 168, severity: "", status: "", source: "", camera: "", q: "" };
  const demo = ctx.mode === "demo";
  if (demo) state.hours = 0;

  const rangeSeg = h("div", { class: "seg", role: "group", "aria-label": "Date range" });
  RANGES.forEach(([label, hrs]) => rangeSeg.append(h("button", { "aria-pressed": String(state.hours === hrs), text: label, disabled: demo && hrs !== 0,
    onclick: (e) => { state.hours = hrs; state.page = 1; rangeSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.target))); load(); } })));
  const select = (key, label, options) => {
    const s = h("select", { class: "input", "aria-label": label, onchange: () => { state[key] = s.value; state.page = 1; load(); } },
      h("option", { value: "", text: `All` }), options.map(([v, t]) => h("option", { value: v, text: t })));
    return h("label", { class: "field" }, label, s);
  };
  let searchTimer;
  const search = h("input", { class: "input", type: "search", placeholder: "ID, reason, class, note…", "aria-label": "Search incidents",
    oninput: () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.q = search.value.trim(); state.page = 1; load(); }, 300); } });
  const { cameras } = await ctx.api.cameras();
  const exportBtn = h("a", { class: "btn", href: "#", onclick: (e) => {
    const url = ctx.api.exportUrl(query());
    if (!url || !ctx.can("analyst")) { e.preventDefault(); ctx.toast(demo ? "Export is disabled for demo data." : "Export requires the analyst role.", "error"); return; }
    e.currentTarget.href = url; } }, icon("download"), "Export CSV");

  root.append(h("div", { class: "filters", role: "search" },
    h("div", { class: "field" }, "Range", rangeSeg),
    select("severity", "Severity", SEVS.map((s) => [s, s])),
    select("status", "Status", STATUSES.map((s) => [s, fmt.label(s)])),
    select("source", "Source", [["live", "Live"], ["recorded", "Recorded"], ["demo", "Demo"]]),
    select("camera", "Camera", cameras.map((c) => [c.camera_id, c.name])),
    h("label", { class: "field", style: { flex: "1", minWidth: "200px" } }, "Search", search),
    exportBtn));
  if (demo) root.append(h("div", { class: "note warn", text: "Demo data: these incidents are reconstructed from precomputed detections on research videos. They are not live detections and cannot be edited." }));

  const tableCard = h("section", { class: "card", style: { padding: "0" } });
  const pager = h("div", { class: "pager" });
  root.append(tableCard, pager);

  function query() {
    const q = { page: state.page, per_page: state.per_page, sort: state.sort, order: state.order };
    if (state.hours) q.since = new Date(Date.now() - state.hours * 3600e3).toISOString().slice(0, 19);
    for (const k of ["severity", "status", "source", "camera", "q"]) if (state[k]) q[k] = state[k];
    return q;
  }

  const COLS = [["id", "Incident", false], ["started_at", "Started", true], ["camera_id", "Camera", true], ["source", "Source", false],
    ["severity", "Severity", true], ["peak_score", "Peak", true], ["primary_reason", "Primary signal", false], ["status", "Status", true], ["evidence", "Evidence", false]];

  async function load() {
    tableCard.style.opacity = ".55";
    try {
      const res = await ctx.api.incidents(query());
      clear(tableCard);
      if (!res.items.length) {
        tableCard.append(h("div", { class: "empty" }, h("b", { text: "No incidents match these filters." }),
          h("div", { text: demo ? "" : "Incidents are opened automatically when a camera's threat score reaches the configured threshold." })));
      } else {
        tableCard.append(h("div", { class: "table-wrap", style: { border: "0" } }, h("table", {},
          h("caption", { class: "sr-only", text: "Incidents" }),
          h("thead", {}, h("tr", {}, COLS.map(([k, label, sortable]) => h("th", { class: k === "peak_score" ? "num" : "",
            "aria-sort": state.sort === k ? (state.order === "asc" ? "ascending" : "descending") : null },
            sortable ? h("button", { onclick: () => { state.order = state.sort === k && state.order === "desc" ? "asc" : "desc"; state.sort = k; load(); } },
              label, state.sort === k ? (state.order === "asc" ? " ↑" : " ↓") : "") : label)))),
          h("tbody", {}, res.items.map((i) => h("tr", { "data-clickable": "", tabindex: 0,
            onclick: () => openDrawer(ctx, i.id, load), onkeydown: (e) => { if (e.key === "Enter") openDrawer(ctx, i.id, load); } },
            h("td", { class: "mono", text: i.id }),
            h("td", { text: i.started_at ? fmt.datetime(i.started_at) : `t=${(i.media_time_start ?? 0).toFixed(1)}s` }),
            h("td", { text: i.camera_id }),
            h("td", {}, srcBadge(i.source_kind)),
            h("td", {}, sevBadge(i.severity)),
            h("td", { class: "num", text: String(i.peak_score) }),
            h("td", { class: "wrap", text: i.primary_reason || "—" }),
            h("td", {}, h("span", { class: "badge", text: fmt.label(i.status) })),
            h("td", { class: "num", text: String(i.evidence_count ?? 0) })))))));
      }
      const pages = Math.max(1, Math.ceil(res.total / res.per_page));
      clear(pager).append(h("span", { text: `${res.total} incident${res.total === 1 ? "" : "s"}` }),
        h("button", { class: "btn small", text: "Previous", disabled: state.page <= 1, onclick: () => { state.page--; load(); } }),
        h("span", { class: "tnum", text: `Page ${state.page} of ${pages}` }),
        h("button", { class: "btn small", text: "Next", disabled: state.page >= pages, onclick: () => { state.page++; load(); } }));
    } catch (e) {
      ctx.toast(`Could not load incidents: ${e.message}`, "error");
    } finally { tableCard.style.opacity = ""; }
  }
  await load();
  const deep = ctx.params.get("id");
  if (deep) openDrawer(ctx, deep, load);
}

async function openDrawer(ctx, id, reload) {
  closeDrawer();
  const backdrop = h("div", { class: "drawer-backdrop", onclick: closeDrawer });
  const drawer = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": "drawer-title", tabindex: -1 });
  const onKey = (e) => { if (e.key === "Escape") closeDrawer(); };
  document.addEventListener("keydown", onKey, { once: true });
  document.body.append(backdrop, drawer);
  drawer.append(h("p", { class: "muted", text: "Loading…" }));
  drawer.focus();
  let inc;
  try { inc = await ctx.api.incident(id); } catch (e) { clear(drawer).append(h("p", { text: e.message })); return; }
  if (!inc) { clear(drawer).append(h("p", { text: "Incident not found." })); return; }
  clear(drawer);
  drawer.append(h("div", { style: { display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" } },
    h("h2", { id: "drawer-title", class: "mono", text: inc.id }), sevBadge(inc.severity), srcBadge(inc.source_kind),
    h("button", { class: "btn small ghost", style: { marginLeft: "auto" }, text: "Close", onclick: closeDrawer })));

  const facts = [
    ["Camera", inc.camera_id], ["Video", inc.video_ref || "—"],
    ["Started", inc.started_at ? fmt.datetime(inc.started_at) : "—"], ["Duration", inc.started_at ? fmt.duration(inc.started_at, inc.ended_at) : `${((inc.media_time_end ?? 0) - (inc.media_time_start ?? 0)).toFixed(1)}s of video`],
    ["Peak score", inc.peak_score], ["Frames", inc.frame_start ? `${inc.frame_start}–${inc.frame_end ?? "…"}` : "—"],
    ["Model", inc.model_version || "—"], ["Engine", inc.engine_version || "—"],
    ["Classes", (inc.detection_classes || []).join(", ") || "—"], ["Max object conf.", inc.max_confidence ? inc.max_confidence.toFixed(2) : "—"],
  ];
  drawer.append(h("div", { class: "kv" }, facts.map(([k, v]) => h("div", {}, h("b", { text: String(v), title: String(v), style: { fontSize: "14px" } }), h("span", { text: k })))));
  drawer.append(h("section", {}, h("h3", { text: "Signals observed" }),
    h("ul", { class: "signal-list" }, (inc.reasons || []).map((r) => h("li", {}, icon("alert"), h("span", { text: r })))),
    inc.rules?.length ? h("p", { class: "muted", style: { fontSize: "12px" }, text: `Rules: ${inc.rules.map((r) => RULE_LABELS[r] || r).join(", ")}` }) : null));

  // evidence
  const ev = h("section", {}, h("h3", { text: "Evidence" }));
  if (!inc.evidence?.length) ev.append(h("p", { class: "muted", text: ctx.mode === "demo" ? "Demo incidents have no stored evidence." : "No evidence files were stored for this incident." }));
  else if (!ctx.can("analyst")) ev.append(h("p", { class: "muted", text: "Evidence access requires the analyst role." }));
  else ev.append(h("div", { class: "evidence-grid" }, inc.evidence.map((e) => {
    const url = ctx.api.evidenceUrl(e.id);
    return h("figure", {}, e.kind === "clip" ? h("video", { src: url, controls: true, preload: "metadata" }) : h("img", { src: url, alt: `Snapshot ${e.id} for ${inc.id}`, loading: "lazy" }),
      h("figcaption", {}, `${e.kind} · ${fmt.compact(e.size_bytes)}B · `, h("a", { href: `${url}?download=1`, text: "download" }),
        h("div", { class: "mono", title: "SHA-256 of the stored file", text: `sha256 ${e.sha256.slice(0, 16)}…` })));
  })));
  drawer.append(ev);

  // review form
  if (ctx.can("analyst")) {
    const status = h("select", { class: "input" }, STATUSES.map((s) => h("option", { value: s, text: fmt.label(s), selected: s === inc.status })));
    const assignee = h("input", { class: "input", maxlength: 120, value: inc.assigned_to || "", placeholder: "Assignee" });
    const notes = h("textarea", { class: "input", maxlength: 4000, placeholder: "Review notes" });
    notes.value = inc.notes || "";
    drawer.append(h("section", {}, h("h3", { text: "Review" }),
      h("div", { class: "grid g-2", style: { gap: "10px" } }, h("label", { class: "field" }, "Status", status), h("label", { class: "field" }, "Assigned to", assignee)),
      h("label", { class: "field", style: { marginTop: "10px" } }, "Notes", notes),
      h("div", { style: { marginTop: "10px", display: "flex", gap: "8px" } },
        h("button", { class: "btn primary", text: "Save review", onclick: async () => {
          try {
            await ctx.api.updateIncident(inc.id, { status: status.value, notes: notes.value, assigned_to: assignee.value || null });
            ctx.toast("Incident updated — change recorded in the audit log.");
            reload(); openDrawer(ctx, inc.id, reload);
          } catch (e) { ctx.toast(e.message, "error"); }
        } }))));
  } else {
    drawer.append(h("p", { class: "muted", text: `Status: ${fmt.label(inc.status)}${inc.notes ? ` — ${inc.notes}` : ""}` }));
  }

  if (inc.detections?.length) {
    drawer.append(h("section", {}, h("h3", { text: `Sampled detections (${inc.detections.length})` }),
      h("div", { class: "table-wrap", style: { maxHeight: "260px" } }, h("table", {},
        h("thead", {}, h("tr", {}, ["Time", "Frame", "Class", "Conf.", "Track", "Score"].map((x, i) => h("th", { class: i ? "num" : "", text: x })))),
        h("tbody", {}, inc.detections.slice(0, 200).map((d) => h("tr", {},
          h("td", { text: fmt.time(d.ts) }), h("td", { class: "num", text: d.frame_idx ?? "—" }), h("td", { class: "num", text: d.class_name }),
          h("td", { class: "num", text: d.confidence.toFixed(2) }), h("td", { class: "num", text: d.track_id ?? "—" }), h("td", { class: "num", text: d.threat_score ?? "—" }))))))));
  }
  if (inc.audit?.length) {
    drawer.append(h("section", {}, h("h3", { text: "Audit trail" }), h("ol", { class: "timeline" }, inc.audit.map((a) =>
      h("li", {}, h("b", { text: a.action }), ` by ${a.actor} · `, h("span", { class: "muted", text: fmt.datetime(a.ts) }),
        Object.keys(a.details || {}).length ? h("div", { class: "mono muted", text: JSON.stringify(a.details) }) : null)))));
  }
}

function closeDrawer() { document.querySelectorAll(".drawer, .drawer-backdrop").forEach((e) => e.remove()); }
export function destroy() { closeDrawer(); }
