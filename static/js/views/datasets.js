// Datasets — registry of every dataset investigated, with licence and status.
import { h, fmt } from "../lib/dom.js";

export async function render(root, ctx) {
  const p = await ctx.api.platform();
  const ds = p.datasets || [];
  const acquired = ds.filter((d) => d.status === "acquired");
  root.append(h("div", { class: "note", text: `${acquired.length} datasets acquired and validated; ${ds.length - acquired.length} investigated but not acquired (reason recorded). Object-detection boxes and video-level anomaly labels are kept strictly separate.` }));
  const card = (d) => h("article", { class: "card ds-card" },
    h("div", { style: { display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" } },
      h("h3", { text: d.name }),
      h("span", { class: `badge st-${d.status}`, text: d.status === "acquired" ? "Acquired" : "Documented only" }),
      h("span", { class: "badge", text: d.label_type === "bbox" ? "Bounding boxes" : d.label_type === "video_temporal" ? "Temporal video labels" : "Video-level labels" })),
    h("dl", {},
      ...[["Licence", d.license], ["Access", d.access], ["Used for", d.used_for], ["Redistribution", d.redistribution],
        ["Version", d.version], ["Split sizes", d.stats ? Object.entries(d.stats).map(([k, v]) => `${k}: ${typeof v === "object" ? Object.entries(v).map(([a, b]) => `${a} ${b}`).join(", ") : v}`).join(" · ") : null],
        ["Blocker", d.blocker], ["Alternative", d.reproducible_alternative], ["Acquire", d.acquire], ["Notes", d.notes]]
        .filter(([, v]) => v).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { class: k === "Acquire" ? "mono" : "", text: String(v) })]),
      h("dt", { text: "Source" }), h("dd", {}, h("a", { href: d.source_url, rel: "noopener noreferrer", target: "_blank", text: d.source_url }))));
  root.append(h("h2", { style: { margin: "4px 0 0", font: "600 15px var(--font-display)" }, text: "Acquired" }),
    h("div", { class: "grid g-3" }, acquired.map(card)),
    h("h2", { style: { margin: "8px 0 0", font: "600 15px var(--font-display)" }, text: "Investigated, not acquired" }),
    h("div", { class: "grid g-3" }, ds.filter((d) => d.status !== "acquired").map(card)));
  const models = p.models || [];
  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Model registry" })),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["ID", "Name", "Schema", "Training data", "Weights", "SHA-256"].map((x) => h("th", { text: x })))),
      h("tbody", {}, models.map((m) => h("tr", {}, h("td", { class: "mono", text: m.id }), h("td", { class: "wrap", text: m.name }),
        h("td", { text: m.schema }), h("td", { class: "mono", text: m.dataset_id ? `${m.dataset_id}@${m.dataset_version || ""}` : "COCO (upstream)" }),
        h("td", { text: m.weights_present ? m.weights : `${m.weights} (missing)` }), h("td", { class: "mono", text: (m.sha256 || "").slice(0, 16) + "…" }))))))));
}
