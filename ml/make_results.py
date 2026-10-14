"""
Render docs/RESULTS.md from the committed evaluation reports, so every
number in the documentation is copied from script output, never typed.

  python -m ml.make_results
"""

import json
import os

from ml.classes import REPO_ROOT


def _load(*parts):
    path = os.path.join(REPO_ROOT, *parts)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}"


def num(x, d=3):
    return "—" if x is None else f"{x:.{d}f}"


def detector_section(det):
    models = list(det["models"])
    L = ["## 1. Object detector (Open Images V7 SentinelX subset, held-out test split)", "",
         f"Test split: {det['dataset']['images']} images, dataset version `{det['dataset']['version']}`; "
         f"photographer-disjoint from training. Protocol: {det['protocol']['ap']}, imgsz {det['protocol']['imgsz']}, "
         f"operating confidence {det['protocol']['operating_conf']}. Source: `reports/detector/report.json`.", "",
         "| Model | mAP50 (shared classes) | mAP50-95 (shared) | mAP50 (all 9, unsupported = 0) | mAP50-95 (all 9) |",
         "|---|---|---|---|---|"]
    for m in models:
        r = det["models"][m]
        L.append(f"| `{m}` | {pct(r['map50_shared'])} | {pct(r['map50_95_shared'])} | {pct(r['map50_all_classes'])} | {pct(r['map50_95_all_classes'])} |")
    L += ["", f"Shared classes = classes both models can output: {', '.join(det['shared_classes'])}. "
          "The COCO baseline has no `firearm` or `helmet` class.", ""]
    b = det.get("bootstrap", {}).get("difference_shared_map50")
    if b and len(models) == 2:
        point = det["models"][models[1]]["map50_shared"] - det["models"][models[0]]["map50_shared"]
        L += [f"Shared-class mAP50 difference ({b['models']}): point estimate **{pct(point)} points**; image-level "
              f"bootstrap ({det['bootstrap']['n_boot']} resamples) mean {pct(b['mean'])}, 95% CI "
              f"[{pct(b['ci95'][0])}, {pct(b['ci95'][1])}], P(Δ ≤ 0) = {b['p_le_0']}.", ""]
    L += ["| Class | test GT boxes | " + " | ".join(f"`{m}` AP50" for m in models) + " | " +
          " | ".join(f"`{m}` P / R / img-FPR" for m in models) + " |",
          "|---|---|" + "---|" * (2 * len(models))]
    for c, n in det["dataset"]["gt_boxes"].items():
        ap = [num(det["models"][m]["per_class"][c]["ap50"]) if det["models"][m]["per_class"][c]["supported"] else "n/a" for m in models]
        op = []
        for m in models:
            e = det["models"][m]["per_class"][c]
            op.append(f"{num(e['precision'], 2)} / {num(e['recall'], 2)} / {num(e['image_fpr'], 3)}" if e["supported"] else "n/a")
        L.append(f"| {c} | {n} | " + " | ".join(ap) + " | " + " | ".join(op) + " |")
    L += ["", "**Latency** (end-to-end per image, batch 1; the median is the fair comparison — GPU means include "
          "warm-up/scheduling outliers visible in p95):", "",
          "| Model | Device | median ms | mean ms | p95 ms | FPS (from mean) |", "|---|---|---|---|---|---|"]
    for m in models:
        for lt in det["models"][m]["latency"]:
            L.append(f"| `{m}` | {lt['device']} | {lt['p50_ms']} | {lt['mean_ms']} | {lt['p95_ms']} | {lt['fps']} |")
    L += ["", "Confusion matrices: `reports/detector/confusion_<model>.png`.", ""]
    return L


def pipeline_section(pipe, temporal):
    L = ["## 2. End-to-end threat pipeline (video-level labels, held-out test splits)", "",
         "ATMA-V test: 21 videos (group-aware split, never used for fitting or model selection). "
         "SCFD test: 179 clips (cross-dataset; fights vs. non-fights). Incident threshold = score ≥ 50, "
         "HIGH = score ≥ 75. Source: `reports/pipeline/report.json`.", "",
         "| Variant | Detector | ATMA-V frame AUC | P / R / FPR @ 50 | P / R / FPR @ 75 (HIGH) | Video detection rate | Videos with a false alarm | False alarms / min of normal video | SCFD clip AUC | SCFD R / FPR @ 50 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for v, d in pipe["variants"].items():
        a, s = d["atmav"].get("test"), d["scfd"].get("test")
        if not a:
            continue
        i, h = a["at_incident_threshold"], a["at_high"]
        L.append(f"| `{v}` | `{d['detector']}` | {num(a['frame_auc'])} | {num(i['precision'], 2)} / {num(i['recall'], 2)} / {num(i['fpr'], 2)} | "
                 f"{num(h['precision'], 2)} / {num(h['recall'], 2)} / {num(h['fpr'], 2)} | {num(a['video_detection_rate'], 2)} | "
                 f"{num(a['videos_with_false_alarm'], 2)} | {num(a['false_alarms_per_min_normal']['incident'], 2)} | "
                 f"{num(s['clip_auc_peak_score'])} | {num(s['at_incident_threshold']['recall'], 2)} / {num(s['at_incident_threshold']['fpr'], 2)} |")
    L.append("")
    for model, t in temporal.items():
        if t:
            c = t["chosen"]
            L += [f"Temporal head for `{model}`: fitted on {t['train']['videos']} ATMA-V train videos "
                  f"({t['train']['frames']} frames), selected on {t['val']['videos']} val videos — feature group "
                  f"`{c['features']}`, C = {c['C']}, val AUC {c['val_auc']}, threshold {c['threshold']} ({c['threshold_rule']}). "
                  f"Source: `reports/temporal/{model}.json`.", ""]
    return L


def main():
    det = _load("reports", "detector", "report.json")
    pipe = _load("reports", "pipeline", "report.json")
    temporal = {m: _load("reports", "temporal", f"{m}.json") for m in ("yolov8n-coco", "sentinelx-det-v1")}
    train = _load("reports", "training", "sentinelx-det-v1", "train_meta.json")
    L = ["# SentinelX — measured results", "",
         "_Generated by `python -m ml.make_results` from the JSON reports in `reports/`. "
         "Do not edit by hand; re-run the evaluation scripts and regenerate._", ""]
    if train:
        L += ["## 0. Training run", "",
              f"`{train['run_id']}`: fine-tuned from `{train['config']['base_weights']}` on dataset "
              f"`{train['dataset']['id']}` version `{train['dataset']['version']}` "
              f"(train/val/test images: {train['dataset']['splits']}), device `{train['effective_train_args']['device']}`, "
              f"{train['effective_train_args']['epochs']} max epochs (patience {train['effective_train_args']['patience']}), "
              f"wall time {train['wall_time_seconds'] / 3600:.1f} h, commit `{train['git_commit'][:7]}`. "
              f"Weights `{train['weights']}` (SHA-256 `{train['weights_sha256'][:16]}…`). "
              "Per-epoch curves: `reports/training/sentinelx-det-v1/results.csv`.", ""]
    if det:
        L += detector_section(det)
    if pipe:
        L += pipeline_section(pipe, temporal)
    out = os.path.join(REPO_ROOT, "docs", "RESULTS.md")
    with open(out, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
