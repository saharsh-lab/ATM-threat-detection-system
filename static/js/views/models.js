// Model Lab — measured results only. Everything here is read from the
// evaluation reports written by ml/evaluate_detector.py and
// ml/evaluate_pipeline.py; the UI never computes or invents metrics.
import { h, fmt } from "../lib/dom.js";
import { columnChart, seriesColors } from "../lib/charts.js";

const pct = (x) => (x === null || x === undefined ? "—" : (x * 100).toFixed(1));

export async function render(root, ctx) {
  const [det, pipe] = await Promise.all([ctx.api.report("detector").catch(() => null), ctx.api.report("pipeline").catch(() => null)]);
  root.append(h("div", { class: "note", text: "All figures on this page are loaded from committed evaluation reports (reports/detector, reports/pipeline) produced by the evaluation scripts. Nothing is entered by hand." }));
  if (!det) root.append(h("div", { class: "card empty", text: "Detector report not generated yet — run: python -m ml.evaluate_detector" }));
  else renderDetector(root, det);
  if (!pipe) root.append(h("div", { class: "card empty", text: "Pipeline report not generated yet — run: python -m ml.evaluate_pipeline" }));
  else renderPipeline(root, pipe);
}

function renderDetector(root, det) {
  const models = Object.keys(det.models);
  const colors = seriesColors();
  const [base, cand] = [models[0], models[models.length - 1]];
  const B = det.models[base], N = det.models[cand];
  const delta = (a, b) => (a === null || b === null || a === undefined || b === undefined ? null : b - a);
  const tile = (label, a, b, fmtv, foot) => {
    const d = delta(a, b);
    return h("div", { class: "card stat" }, h("div", { class: "label", text: label }),
      h("div", { class: "value", text: fmtv(b) }),
      h("div", { class: "foot" }, `${base}: ${fmtv(a)} · `, d === null ? "" : h("span", { class: d >= 0 ? "delta-up" : "delta-down", text: `${d >= 0 ? "+" : ""}${fmtv(d)}` }), foot ? ` ${foot}` : ""));
  };
  const lat = (m, dev) => (m.latency || []).find((l) => l.device === dev)?.mean_ms;
  const devs = [...new Set(models.flatMap((m) => det.models[m].latency.map((l) => l.device)))];
  const ci = det.bootstrap?.difference_shared_map50;
  root.append(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "Object detector" }),
      h("span", { class: "sub", text: `${det.dataset.id} · ${det.dataset.split} split · ${det.dataset.images} images · version ${det.dataset.version}` })),
    h("div", { class: "grid g-4" },
      tile("mAP50, shared classes", B.map50_shared, N.map50_shared, (v) => (v === null ? "—" : `${pct(v)}`), "pts"),
      tile("mAP50-95, shared classes", B.map50_95_shared, N.map50_95_shared, (v) => (v === null ? "—" : `${pct(v)}`), "pts"),
      tile("mAP50, all 9 classes", B.map50_all_classes, N.map50_all_classes, (v) => (v === null ? "—" : `${pct(v)}`), "pts (unsupported = 0)"),
      tile(`Latency (${devs[0]})`, lat(B, devs[0]), lat(N, devs[0]), (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)} ms`), "")),
    ci ? h("p", { class: "muted", style: { fontSize: "12.5px", margin: "14px 0 0" },
      text: `Bootstrap (${det.bootstrap.n_boot} image resamples): shared-class mAP50 difference ${ci.models} = ${pct(ci.mean)} pts, 95% CI [${pct(ci.ci95[0])}, ${pct(ci.ci95[1])}]. Shared classes: ${det.shared_classes.join(", ")}.` }) : null));

  const classes = Object.keys(N.per_class);
  const chart = h("div");
  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "AP50 by class" }),
    h("span", { class: "sub", text: "n/a = the model has no such class" })), chart));
  columnChart(chart, { categories: classes, height: 250, max: 1, format: (v) => v.toFixed(2), caption: "AP50 by class",
    series: models.map((m, i) => ({ name: m, color: colors[i], values: classes.map((c) => (det.models[m].per_class[c].supported ? det.models[m].per_class[c].ap50 : null)) })) });

  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: `Operating point (confidence ≥ ${det.protocol.operating_conf})` }),
    h("span", { class: "sub", text: "precision · recall · image-level false-positive rate" })),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Class" }), h("th", { class: "num", text: "GT boxes" }),
        models.flatMap((m) => [h("th", { class: "num", text: `${m} P` }), h("th", { class: "num", text: "R" }), h("th", { class: "num", text: "img FPR" })]))),
      h("tbody", {}, classes.map((c) => h("tr", {}, h("td", { text: c }), h("td", { class: "num", text: String(det.dataset.gt_boxes[c]) }),
        models.flatMap((m) => {
          const e = det.models[m].per_class[c];
          return e.supported ? [h("td", { class: "num", text: fmt.num(e.precision, 3) }), h("td", { class: "num", text: fmt.num(e.recall, 3) }), h("td", { class: "num", text: fmt.num(e.image_fpr, 3) })]
            : [h("td", { class: "num muted", text: "n/a", colspan: 3 })];
        }))))))));

  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Inference latency" }), h("span", { class: "sub", text: "end-to-end per image, batch 1, imgsz " + det.protocol.imgsz })),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Model", "Device", "Mean ms", "p50 ms", "p95 ms", "FPS"].map((x, i) => h("th", { class: i > 1 ? "num" : "", text: x })))),
      h("tbody", {}, models.flatMap((m) => det.models[m].latency.map((l) => h("tr", {}, h("td", { class: "mono", text: m }), h("td", { text: l.device }),
        ...[l.mean_ms, l.p50_ms, l.p95_ms, l.fps].map((v) => h("td", { class: "num", text: String(v) }))))))))));

  root.append(h("section", { class: "grid g-2" }, models.map((m) => h("figure", { class: "card", style: { margin: 0 } },
    h("div", { class: "card-head" }, h("h3", { text: `Confusion matrix · ${m}` })),
    h("img", { class: "figure-img", src: `/static/reports/detector/confusion_${m}.png`, alt: `Confusion matrix for ${m}; values are also in the report JSON`, loading: "lazy" })))));
}

function renderPipeline(root, rep) {
  const variants = Object.keys(rep.variants);
  const colors = seriesColors();
  const split = "test";
  const a = (v) => rep.variants[v].atmav?.[split];
  const s = (v) => rep.variants[v].scfd?.[split];
  const chart = h("div");
  root.append(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "End-to-end threat pipeline" }),
    h("span", { class: "sub", text: `video-level labels · ${split} split · incident threshold ${rep.protocol.incident_threshold}` })),
    h("p", { class: "muted", style: { fontSize: "12.5px", marginTop: 0 }, text: rep.protocol.zones }), chart,
    h("div", { class: "table-wrap", style: { marginTop: "14px" } }, h("table", {},
      h("thead", {}, h("tr", {}, ["Variant", "Detector", "ATMA-V frame AUC", "P @50", "R @50", "FPR @50", "Video detection", "False alarms / min", "SCFD clip AUC", "SCFD FPR @50"].map((x, i) => h("th", { class: i > 1 ? "num" : "", text: x })))),
      h("tbody", {}, variants.map((v) => h("tr", {}, h("td", { class: "mono", text: v }), h("td", { class: "mono", text: rep.variants[v].detector }),
        ...[a(v)?.frame_auc, a(v)?.at_incident_threshold.precision, a(v)?.at_incident_threshold.recall, a(v)?.at_incident_threshold.fpr,
          a(v)?.video_detection_rate, a(v)?.false_alarms_per_min_normal.incident, s(v)?.clip_auc_peak_score, s(v)?.at_incident_threshold.fpr]
          .map((x) => h("td", { class: "num", text: x === null || x === undefined ? "—" : String(x) })))))))));
  const metrics = [["ATMA-V AUC", (v) => a(v)?.frame_auc], ["ATMA-V F1 @50", (v) => a(v)?.at_incident_threshold.f1],
    ["1 − FPR @50", (v) => (a(v)?.at_incident_threshold.fpr === undefined ? null : 1 - a(v).at_incident_threshold.fpr)], ["SCFD AUC", (v) => s(v)?.clip_auc_peak_score]];
  columnChart(chart, { categories: metrics.map((m) => m[0]), max: 1, height: 230, format: (x) => x.toFixed(2), caption: "Pipeline comparison",
    series: variants.map((v, i) => ({ name: v, color: colors[i % colors.length], values: metrics.map(([, f]) => f(v) ?? null) })) });
}
