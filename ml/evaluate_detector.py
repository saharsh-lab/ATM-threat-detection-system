"""
Evaluate object detectors on the SentinelX held-out test split with ONE
implementation for every model, so baseline and fine-tuned numbers are
directly comparable.

  python -m ml.evaluate_detector --models yolov8n-coco sentinelx-det-v1 --split test

Metrics (per class and averaged):
  AP50, AP50-95      COCO-style 101-point interpolated AP (IoU .50:.05:.95)
  P / R / F1         at the operating confidence (default 0.25, same for all models)
  image FPR          share of images WITHOUT the class where the model reports it
  FPPI               false-positive boxes per image
  confusion matrix   greedy IoU≥0.5 matching, rows=predicted, cols=true (+background)
  95% CI             image-level bootstrap for mAP50 and the per-model difference
  latency            end-to-end per-image (preprocess+inference+NMS) on each device

The COCO baseline is mapped to SentinelX classes through data/registry/
classes.yaml; classes it cannot emit (firearm, helmet) are reported as
"unsupported" and excluded from the shared-class mAP.
"""

import argparse
import glob
import json
import os
import platform
import time
from datetime import datetime, timezone

import numpy as np

from ml.classes import REPO_ROOT, class_names, coco_to_class

IOU_THRESHOLDS = np.linspace(0.5, 0.95, 10)
DATASET_DIR = os.path.join(REPO_ROOT, "datasets", "processed", "openimages_v7_sentinelx_v1")


# ── geometry / AP ────────────────────────────────────────────────────────

def box_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU matrix between xyxy boxes a (N,4) and b (M,4)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    tl = np.maximum(a[:, None, :2], b[None, :, :2])
    br = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.prod(np.clip(br - tl, 0, None), axis=2)
    area_a = np.prod(a[:, 2:] - a[:, :2], axis=1)
    area_b = np.prod(b[:, 2:] - b[:, :2], axis=1)
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-12)


def match_image(pred_boxes, pred_cls, pred_conf, gt_boxes, gt_cls):
    """Per-prediction TP flags at each IoU threshold (class-aware, greedy by conf)."""
    tp = np.zeros((len(pred_boxes), len(IOU_THRESHOLDS)), dtype=bool)
    if len(pred_boxes) == 0 or len(gt_boxes) == 0:
        return tp
    iou = box_iou(pred_boxes, gt_boxes)
    order = np.argsort(-pred_conf)
    for t, thr in enumerate(IOU_THRESHOLDS):
        used = np.zeros(len(gt_boxes), dtype=bool)
        for i in order:
            cand = np.where((gt_cls == pred_cls[i]) & ~used & (iou[i] >= thr))[0]
            if len(cand):
                j = cand[np.argmax(iou[i, cand])]
                used[j] = True
                tp[i, t] = True
    return tp


def average_precision(tp: np.ndarray, conf: np.ndarray, n_gt: int) -> np.ndarray:
    """AP at each IoU threshold, COCO 101-point interpolation."""
    if n_gt == 0:
        return np.full(len(IOU_THRESHOLDS), np.nan)
    if len(conf) == 0:
        return np.zeros(len(IOU_THRESHOLDS))
    order = np.argsort(-conf, kind="stable")
    tp = tp[order]
    tpc = np.cumsum(tp, axis=0)
    fpc = np.cumsum(~tp, axis=0)
    recall = tpc / n_gt
    precision = tpc / (tpc + fpc)
    ap = np.zeros(len(IOU_THRESHOLDS))
    rs = np.linspace(0, 1, 101)
    for t in range(len(IOU_THRESHOLDS)):
        p = np.maximum.accumulate(precision[::-1, t])[::-1]     # precision envelope
        idx = np.searchsorted(recall[:, t], rs, side="left")
        ap[t] = np.mean([p[i] if i < len(p) else 0.0 for i in idx])
    return ap


# ── data ─────────────────────────────────────────────────────────────────

def load_split(split: str):
    import cv2
    images = sorted(glob.glob(os.path.join(DATASET_DIR, "images", split, "*.jpg")))
    gts = []
    for path in images:
        h, w = cv2.imread(path).shape[:2]
        lbl = os.path.join(DATASET_DIR, "labels", split, os.path.basename(path)[:-4] + ".txt")
        rows = np.loadtxt(lbl, ndmin=2) if os.path.getsize(lbl) else np.zeros((0, 5))
        cls = rows[:, 0].astype(int)
        cx, cy, bw, bh = rows[:, 1] * w, rows[:, 2] * h, rows[:, 3] * w, rows[:, 4] * h
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        gts.append({"path": path, "boxes": boxes, "cls": cls})
    return gts


# ── prediction ───────────────────────────────────────────────────────────

def predict_all(model_id: str, gts: list, device: str, imgsz: int, batch: int = 16):
    from ultralytics import YOLO
    from core.detector import _abs, resolve_model
    spec = resolve_model(model_id)
    model = YOLO(_abs(spec["weights"]))
    if spec["schema"] == "coco":
        cmap = coco_to_class()
        classes = sorted(cmap)
    else:
        cmap = {i: i for i in range(len(class_names()))}
        classes = None
    preds = []
    paths = [g["path"] for g in gts]
    for i in range(0, len(paths), batch):
        res = model.predict(paths[i:i + batch], conf=0.001, iou=0.7, imgsz=imgsz, device=device,
                            classes=classes, verbose=False, max_det=300)
        for r in res:
            b = r.boxes
            c = b.cls.int().cpu().numpy()
            keep = np.array([k in cmap for k in c], dtype=bool)
            preds.append({"boxes": b.xyxy.cpu().numpy()[keep],
                          "cls": np.array([cmap[k] for k in c[keep]], dtype=int),
                          "conf": b.conf.cpu().numpy()[keep]})
    supported = sorted(set(cmap.values()))
    return spec, preds, supported


def measure_latency(model_id: str, gts: list, device: str, imgsz: int, n: int = 200, warmup: int = 10):
    from ultralytics import YOLO
    from core.detector import _abs, resolve_model
    import cv2
    model = YOLO(_abs(resolve_model(model_id)["weights"]))
    frames = [cv2.imread(g["path"]) for g in gts[:n + warmup]]
    times = []
    for i, f in enumerate(frames):
        t0 = time.perf_counter()
        model.predict(f, conf=0.25, imgsz=imgsz, device=device, verbose=False)
        if i >= warmup:
            times.append((time.perf_counter() - t0) * 1000)
    t = np.array(times)
    return {"device": device, "images": len(t), "mean_ms": round(float(t.mean()), 2),
            "p50_ms": round(float(np.percentile(t, 50)), 2), "p95_ms": round(float(np.percentile(t, 95)), 2),
            "fps": round(1000 / float(t.mean()), 1)}


# ── metrics ──────────────────────────────────────────────────────────────

def per_image_records(gts, preds):
    recs = []
    for g, p in zip(gts, preds):
        tp = match_image(p["boxes"], p["cls"], p["conf"], g["boxes"], g["cls"])
        recs.append({"tp": tp, "conf": p["conf"], "cls": p["cls"], "gt_cls": g["cls"]})
    return recs


def class_ap(recs, idx, n_classes):
    """AP[c, t] over the subset of images idx."""
    ap = np.full((n_classes, len(IOU_THRESHOLDS)), np.nan)
    tp = np.concatenate([recs[i]["tp"] for i in idx]) if len(idx) else np.zeros((0, 10), bool)
    conf = np.concatenate([recs[i]["conf"] for i in idx]) if len(idx) else np.zeros(0)
    cls = np.concatenate([recs[i]["cls"] for i in idx]) if len(idx) else np.zeros(0, int)
    gt_cls = np.concatenate([recs[i]["gt_cls"] for i in idx]) if len(idx) else np.zeros(0, int)
    for c in range(n_classes):
        m = cls == c
        ap[c] = average_precision(tp[m], conf[m], int((gt_cls == c).sum()))
    return ap


def operating_point(gts, preds, recs, n_classes, conf_thr):
    names = class_names()
    out = {}
    n_img = len(gts)
    for c in range(n_classes):
        tp = fp = 0
        n_gt = 0
        imgs_without, fp_imgs = 0, 0
        for g, p, r in zip(gts, preds, recs):
            m = (p["cls"] == c) & (p["conf"] >= conf_thr)
            t = r["tp"][m, 0]
            tp += int(t.sum())
            fp += int((~t).sum())
            n_gt += int((g["cls"] == c).sum())
            if not (g["cls"] == c).any():
                imgs_without += 1
                fp_imgs += int(m.any())
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / n_gt if n_gt else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
        out[names[c]] = {"gt_boxes": n_gt, "tp": tp, "fp": fp, "precision": _r(prec), "recall": _r(rec),
                         "f1": _r(f1), "fppi": _r(fp / n_img), "image_fpr": _r(fp_imgs / imgs_without if imgs_without else None)}
    return out


def confusion(gts, preds, n_classes, conf_thr, iou_thr=0.5):
    """(n+1)x(n+1): rows predicted class (last=background/missed), cols true class (last=background/FP)."""
    m = np.zeros((n_classes + 1, n_classes + 1), dtype=int)
    for g, p in zip(gts, preds):
        keep = p["conf"] >= conf_thr
        pb, pc = p["boxes"][keep], p["cls"][keep]
        iou = box_iou(pb, g["boxes"])
        used_p, used_g = set(), set()
        pairs = sorted(((iou[i, j], i, j) for i in range(len(pb)) for j in range(len(g["boxes"]))
                        if iou[i, j] >= iou_thr), reverse=True)
        for _, i, j in pairs:
            if i in used_p or j in used_g:
                continue
            used_p.add(i); used_g.add(j)
            m[pc[i], g["cls"][j]] += 1
        for i in range(len(pb)):
            if i not in used_p:
                m[pc[i], n_classes] += 1
        for j in range(len(g["boxes"])):
            if j not in used_g:
                m[n_classes, g["cls"][j]] += 1
    return m


def _r(x, k=4):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), k)


def bootstrap_map50(recs_by_model: dict, classes_by_model: dict, n_classes: int, n_boot: int, seed: int = 0):
    """Image-level bootstrap of mAP50 (over each model's supported classes, and
    over the classes shared by all models) and of the pairwise difference."""
    rng = np.random.default_rng(seed)
    n = len(next(iter(recs_by_model.values())))
    shared = sorted(set.intersection(*[set(v) for v in classes_by_model.values()]))
    samples = {m: {"own": [], "shared": []} for m in recs_by_model}
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        for m, recs in recs_by_model.items():
            ap50 = class_ap(recs, idx, n_classes)[:, 0]
            samples[m]["own"].append(np.nanmean(ap50[classes_by_model[m]]))
            samples[m]["shared"].append(np.nanmean(ap50[shared]))
    out = {"n_boot": n_boot, "shared_classes": [class_names()[c] for c in shared], "models": {}}
    for m, s in samples.items():
        out["models"][m] = {k: {"ci95": [_r(np.percentile(v, 2.5)), _r(np.percentile(v, 97.5))]}
                            for k, v in s.items()}
    models = list(samples)
    if len(models) == 2:
        d = np.array(samples[models[1]]["shared"]) - np.array(samples[models[0]]["shared"])
        out["difference_shared_map50"] = {"models": f"{models[1]} - {models[0]}", "mean": _r(d.mean()),
                                          "ci95": [_r(np.percentile(d, 2.5)), _r(np.percentile(d, 97.5))],
                                          "p_le_0": _r(float((d <= 0).mean()))}
    return out


def pycocotools_check(gts, preds, n_classes, ours_ap):
    """Re-score the same predictions with the reference COCO implementation.
    Returns max |difference| in per-class AP50 / AP50-95, or None if
    pycocotools is not installed."""
    try:
        import contextlib
        import io
        from pycocotools.coco import COCO
        from pycocotools.cocoeval import COCOeval
    except ImportError:
        return None
    images, anns, dets, aid = [], [], [], 1
    for i, (g, p) in enumerate(zip(gts, preds)):
        images.append({"id": i})
        for b, c in zip(g["boxes"], g["cls"]):
            w, hh = b[2] - b[0], b[3] - b[1]
            anns.append({"id": aid, "image_id": i, "category_id": int(c), "iscrowd": 0, "area": float(w * hh),
                         "bbox": [float(b[0]), float(b[1]), float(w), float(hh)]})
            aid += 1
        for b, c, sc in zip(p["boxes"], p["cls"], p["conf"]):
            dets.append({"image_id": i, "category_id": int(c), "score": float(sc),
                         "bbox": [float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])]})
    if not dets:
        return None
    gt = COCO()
    gt.dataset = {"images": images, "annotations": anns, "categories": [{"id": k} for k in range(n_classes)]}
    with contextlib.redirect_stdout(io.StringIO()):
        gt.createIndex()
        ev = COCOeval(gt, gt.loadRes(dets), "bbox")
        ev.params.maxDets = [1, 10, 300]
        ev.evaluate()
        ev.accumulate()
    prec = ev.eval["precision"]
    d50, d = [], []
    for k in range(n_classes):
        pc = prec[:, :, k, 0, -1]
        if (pc[0] > -1).any() and not np.isnan(ours_ap[k, 0]):
            d50.append(abs(pc[0][pc[0] > -1].mean() - ours_ap[k, 0]))
            d.append(abs(pc[pc > -1].mean() - np.nanmean(ours_ap[k])))
    return {"reference": "pycocotools COCOeval (maxDets 300)", "max_abs_diff_ap50": _r(max(d50), 5) if d50 else None,
            "max_abs_diff_ap50_95": _r(max(d), 5) if d else None}


def save_confusion_png(mat, names, title, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = names + ["background"]
    norm = mat / np.maximum(mat.sum(axis=0, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(8.5, 7.5), dpi=120)
    im = ax.imshow(norm, cmap="Reds", vmin=0, vmax=1)
    ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
    ax.set_yticks(range(len(labels)), labels)
    ax.set_xlabel("True class")
    ax.set_ylabel("Predicted class")
    ax.set_title(title)
    for i in range(len(labels)):
        for j in range(len(labels)):
            if mat[i, j]:
                ax.text(j, i, str(mat[i, j]), ha="center", va="center", fontsize=7,
                        color="white" if norm[i, j] > 0.5 else "black")
    fig.colorbar(im, ax=ax, fraction=0.046, label="column-normalised")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+", default=["yolov8n-coco", "sentinelx-det-v1"])
    p.add_argument("--split", default="test")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.25, help="operating confidence for P/R/FPR/confusion")
    p.add_argument("--device", default=None)
    p.add_argument("--latency-devices", nargs="*", default=None)
    p.add_argument("--bootstrap", type=int, default=300)
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "reports", "detector"))
    args = p.parse_args(argv)

    from core.detector import pick_device, sha256_file, _abs, resolve_model
    device = args.device or pick_device()
    latency_devices = args.latency_devices if args.latency_devices is not None else sorted({device, "cpu"})
    names = class_names()
    n = len(names)
    gts = load_split(args.split)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(REPO_ROOT, "data", "manifests", "openimages_v7_sentinelx_v1", "summary.json")) as f:
        ds = json.load(f)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {"id": ds["dataset"], "version": ds["version"], "split": args.split, "images": len(gts),
                    "gt_boxes": {names[c]: int(sum((g["cls"] == c).sum() for g in gts)) for c in range(n)}},
        "protocol": {"imgsz": args.imgsz, "nms_iou": 0.7, "ap_conf_floor": 0.001, "operating_conf": args.conf,
                     "ap": "COCO 101-point, IoU 0.50:0.05:0.95", "device": device},
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "models": {},
    }
    recs_by_model, classes_by_model = {}, {}
    for mid in args.models:
        spec, preds, supported = predict_all(mid, gts, device, args.imgsz)
        recs = per_image_records(gts, preds)
        ap = class_ap(recs, np.arange(len(gts)), n)
        op = operating_point(gts, preds, recs, n, args.conf)
        cm = confusion(gts, preds, n, args.conf)
        save_confusion_png(cm, names, f"{mid} — {args.split} (conf ≥ {args.conf})",
                           os.path.join(args.out, f"confusion_{mid}.png"))
        per_class = {}
        for c in range(n):
            entry = {"supported": c in supported, **op[names[c]]}
            entry["ap50"] = _r(ap[c, 0]) if c in supported else None
            entry["ap50_95"] = _r(np.nanmean(ap[c])) if c in supported else None
            per_class[names[c]] = entry
        report["models"][mid] = {
            "name": spec.get("name", mid), "weights": spec["weights"],
            "weights_sha256": sha256_file(_abs(spec["weights"])), "schema": spec["schema"],
            "supported_classes": [names[c] for c in supported],
            "map50_supported": _r(np.nanmean(ap[supported, 0])),
            "map50_95_supported": _r(np.nanmean(ap[supported])),
            "map50_all_classes": _r(np.nanmean(np.where(np.isin(np.arange(n), supported)[:, None], ap, 0)[:, 0])),
            "map50_95_all_classes": _r(np.nanmean(np.where(np.isin(np.arange(n), supported)[:, None], ap, 0))),
            "per_class": per_class,
            "confusion_matrix": {"labels": names + ["background"], "rows": "predicted", "cols": "true",
                                 "matrix": cm.tolist()},
            "latency": [measure_latency(mid, gts, d, args.imgsz) for d in latency_devices],
        }
        report["models"][mid]["metric_check"] = pycocotools_check(gts, preds, n, ap)
        recs_by_model[mid] = recs
        classes_by_model[mid] = supported
        print(f"{mid}: mAP50(supported)={report['models'][mid]['map50_supported']} "
              f"mAP50-95={report['models'][mid]['map50_95_supported']}")

    shared = sorted(set.intersection(*[set(v) for v in classes_by_model.values()]))
    for mid, recs in recs_by_model.items():
        ap = class_ap(recs, np.arange(len(gts)), n)
        report["models"][mid]["map50_shared"] = _r(np.nanmean(ap[shared, 0]))
        report["models"][mid]["map50_95_shared"] = _r(np.nanmean(ap[shared]))
    report["shared_classes"] = [names[c] for c in shared]
    if args.bootstrap:
        report["bootstrap"] = bootstrap_map50(recs_by_model, classes_by_model, n, args.bootstrap)

    with open(os.path.join(args.out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    write_markdown(report, os.path.join(args.out, "README.md"))
    print(f"wrote {args.out}/report.json")


def write_markdown(rep, path):
    names = list(next(iter(rep["models"].values()))["per_class"])
    models = list(rep["models"])
    L = [f"# Detector evaluation — {rep['dataset']['id']} ({rep['dataset']['split']})", "",
         f"Generated {rep['generated_utc']} by `ml/evaluate_detector.py`. Dataset version "
         f"`{rep['dataset']['version']}`, {rep['dataset']['images']} images. Protocol: imgsz "
         f"{rep['protocol']['imgsz']}, {rep['protocol']['ap']}, operating confidence "
         f"{rep['protocol']['operating_conf']}. All numbers are produced by the script — none are hand-entered.", "",
         "## Summary", "", "| Model | mAP50 (shared classes) | mAP50-95 (shared) | mAP50 (all 9 classes, unsupported=0) | mAP50-95 (all 9) |",
         "|---|---|---|---|---|"]
    for m in models:
        r = rep["models"][m]
        L.append(f"| `{m}` | {r['map50_shared']} | {r['map50_95_shared']} | {r['map50_all_classes']} | {r['map50_95_all_classes']} |")
    L += ["", f"Shared classes (supported by every model): {', '.join(rep['shared_classes'])}.", ""]
    if "bootstrap" in rep:
        b = rep["bootstrap"]
        L.append(f"Image-level bootstrap ({b['n_boot']} resamples), 95% CI of shared-class mAP50:")
        for m in models:
            L.append(f"- `{m}`: {b['models'][m]['shared']['ci95']}")
        if "difference_shared_map50" in b:
            d = b["difference_shared_map50"]
            L.append(f"- difference ({d['models']}): mean {d['mean']}, 95% CI {d['ci95']}, P(Δ ≤ 0) = {d['p_le_0']}")
        L.append("")
    L += ["## Per-class AP50 / AP50-95", "", "| Class | GT boxes | " + " | ".join(f"`{m}` AP50 | `{m}` AP50-95" for m in models) + " |",
          "|---|---|" + "---|---|" * len(models)]
    for c in names:
        row = [c, str(rep["dataset"]["gt_boxes"][c])]
        for m in models:
            e = rep["models"][m]["per_class"][c]
            row += [str(e["ap50"]) if e["supported"] else "unsupported", str(e["ap50_95"]) if e["supported"] else "—"]
        L.append("| " + " | ".join(row) + " |")
    L += ["", f"## Operating point (conf ≥ {rep['protocol']['operating_conf']})", "",
          "| Class | " + " | ".join(f"`{m}` P / R / image-FPR / FPPI" for m in models) + " |",
          "|---|" + "---|" * len(models)]
    for c in names:
        row = [c]
        for m in models:
            e = rep["models"][m]["per_class"][c]
            row.append("unsupported" if not e["supported"] else
                       f"{e['precision']} / {e['recall']} / {e['image_fpr']} / {e['fppi']}")
        L.append("| " + " | ".join(row) + " |")
    L += ["", "## Latency (end-to-end per image, batch 1)", "", "| Model | Device | mean ms | p50 ms | p95 ms | FPS |",
          "|---|---|---|---|---|---|"]
    for m in models:
        for lt in rep["models"][m]["latency"]:
            L.append(f"| `{m}` | {lt['device']} | {lt['mean_ms']} | {lt['p50_ms']} | {lt['p95_ms']} | {lt['fps']} |")
    L += ["", "## Confusion matrices", ""] + [f"![{m}](confusion_{m}.png)" for m in models]
    checks = {m: rep["models"][m].get("metric_check") for m in models}
    if any(checks.values()):
        L += ["", "## Metric implementation check", "",
              "The same predictions were re-scored with the reference `pycocotools` COCOeval:"]
        L += [f"- `{m}`: max |ΔAP50| = {c['max_abs_diff_ap50']}, max |ΔAP50-95| = {c['max_abs_diff_ap50_95']}" for m, c in checks.items() if c]
    L += ["", f"Environment: {rep['environment']['platform']}, Python {rep['environment']['python']}.", ""]
    with open(path, "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
