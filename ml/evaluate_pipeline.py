"""
End-to-end threat-pipeline evaluation on video-level labels.

  python -m ml.evaluate_pipeline --variants v0 v2-coco v2-sentinelx

Variants
  v0            ORIGINAL system, frozen in ml/baseline/: yolov8n COCO, conf 0.30,
                every frame, IoU tracker v0, rules engine v0 (per-frame smoothing).
  v2-coco       yolov8n COCO + ByteTrack + tracker/engine v2 at INFER_FPS
                (isolates the effect of the new tracking/behaviour engine).
  v2-sentinelx  fine-tuned SentinelX detector + ByteTrack + engine v2.

Datasets (labels are video-level — never mixed with detector boxes)
  ATMA-V  frame-level temporal labels; group-aware splits from
          data/manifests/atmav/manifest.json. Only the `test` split is used
          for reported numbers; train/val exist for calibration.
  SCFD    clip-level fight / noFight; `test` split reported.

Metrics
  frame ROC-AUC of the threat score; at the incident threshold (score ≥ 50)
  and at HIGH (≥ 75): precision, recall, F1, false-positive rate on normal
  frames; video-level detection rate / false-alarm rate; false alarms per
  minute of normal footage (rising edges of the alarm state).

Every detector pass is cached in datasets/cache/pipeline/ so engine changes
are re-evaluated without re-running inference.
"""

import argparse
import gzip
import json
import os
import time
from datetime import datetime, timezone

import cv2
import numpy as np

from ml.classes import REPO_ROOT

CACHE = os.path.join(REPO_ROOT, "datasets", "cache", "pipeline")
ATMAV_MANIFEST = os.path.join(REPO_ROOT, "data", "manifests", "atmav", "manifest.json")
SCFD_MANIFEST = os.path.join(REPO_ROOT, "data", "manifests", "scfd", "manifest.json")
SCFD_ROOT = os.path.join(REPO_ROOT, "datasets", "raw", "scfd", "extracted", "fight-detection-surv-dataset-master")
OUT = os.path.join(REPO_ROOT, "reports", "pipeline")
OPEN_SCORE, HIGH_SCORE = 50, 75


# ── detection passes (cached) ────────────────────────────────────────────

def _cache_path(kind, dataset, name):
    return os.path.join(CACHE, kind, dataset, name.replace("/", "__") + ".json.gz")


def run_pass(kind: str, dataset: str, name: str, path: str, infer_fps: float, device: str):
    """kind: 'v0' (baseline predict, every frame) | model id (track at infer_fps)."""
    cp = _cache_path(kind, dataset, name)
    if os.path.exists(cp):
        with gzip.open(cp, "rt") as f:
            return json.load(f)
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 1 or fps > 120:
        fps = 25.0
    frames = []
    if kind == "v0":
        from ml.baseline import config_v0
        model = _model("yolov8n-coco")
        names = model.names
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            r = model.predict(frame, classes=config_v0.YOLO_CLASSES, conf=config_v0.YOLO_CONFIDENCE,
                              device=device, verbose=False)[0]
            frames.append({"f": idx, "d": [[names[int(c)], round(float(s), 4), [int(v) for v in b]]
                                            for c, s, b in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(),
                                                               r.boxes.xyxy.tolist())]})
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    else:
        import config
        from core.detector import Detector
        det = Detector(kind, tracking=True, device=device)
        idx, last_t = 0, -1e9
        step = 1.0 / infer_fps
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            t = idx / fps
            if t - last_t < step:
                continue
            last_t = t
            ds = det.detect(frame)
            frames.append({"f": idx, "d": [[d["class_name"], d["confidence"], list(d["bbox"]), d["track_id"]]
                                            for d in ds]})
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    out = {"fps": fps, "width": w, "height": h, "n_frames": idx, "frames": frames}
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    with gzip.open(cp, "wt") as f:
        json.dump(out, f)
    return out


def motion_pass(dataset: str, name: str, path: str, infer_fps: float):
    """OpenCV cues (motion / brightness / sharpness) on exactly the frames the
    v2 detection pass samples. Cached like detections."""
    cp = _cache_path("motion", dataset, name)
    if os.path.exists(cp):
        with gzip.open(cp, "rt") as f:
            return json.load(f)
    from core.temporal import MotionMeter
    from core.zones import ZoneConfig
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 1 or fps > 120:
        fps = 25.0
    meter = MotionMeter(ZoneConfig())
    out, idx, last_t = {}, 0, -1e9
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        t = idx / fps
        if t - last_t < 1.0 / infer_fps:
            continue
        last_t = t
        out[str(idx)] = {k: round(v, 5) for k, v in meter.update(frame, t).items()}
    cap.release()
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    with gzip.open(cp, "wt") as f:
        json.dump(out, f)
    return out


_MODELS = {}


def _model(model_id):
    if model_id not in _MODELS:
        from ultralytics import YOLO
        from core.detector import _abs, resolve_model
        _MODELS[model_id] = YOLO(_abs(resolve_model(model_id)["weights"]))
    return _MODELS[model_id]


# ── engines on cached detections ─────────────────────────────────────────

def score_v0(p, cues=None) -> np.ndarray:
    from ml.baseline import threat_engine_v0, tracker_v0
    clock = {"t": 0.0}
    tracker_v0.clock = threat_engine_v0.clock = lambda: clock["t"]
    tr, en = tracker_v0.PersonTracker(), threat_engine_v0.ThreatEngine()
    scores = np.zeros(p["n_frames"] + 1)
    for fr in p["frames"]:
        clock["t"] = fr["f"] / p["fps"]
        dets = [{"class_name": c, "confidence": s, "bbox": tuple(b)} for c, s, b in fr["d"]]
        persons = [d for d in dets if d["class_name"] == "person"]
        tracked = tr.update(persons, p["width"], p["height"])
        scores[fr["f"]] = en.evaluate(tracked, dets, p["width"], p["height"])["score"]
    return scores, {}


def v2_frames(p, cues=None, head=None, collect=False):
    """Replay cached detections through tracker v2 → temporal features → engine v2.
    Yields per processed frame: (frame_idx, engine_result, feature_vector|None)."""
    from core.temporal import TemporalFeatures
    from core.threat_engine import ThreatEngine
    from core.tracker import PersonTracker
    tr, en, tf = PersonTracker(), ThreatEngine(), TemporalFeatures()
    for fr in p["frames"]:
        t = fr["f"] / p["fps"]
        dets = [{"class_name": c, "confidence": s, "bbox": tuple(b), "track_id": tid} for c, s, b, tid in fr["d"]]
        persons = [d for d in dets if d["class_name"] == "person"]
        tracked = tr.update(persons, p["width"], p["height"], now=t)
        cue = cues.get(str(fr["f"])) if cues else None
        x = tf.update(t, tracked, dets, cue or {}) if (head is not None or collect) else None
        anomaly = (head.predict(x), head.threshold) if head is not None else None
        res = en.evaluate(tracked, dets, p["width"], p["height"], now=t, cues=cue, anomaly=anomaly)
        if anomaly is not None:
            res["anomaly_p"] = anomaly[0]
        yield fr["f"], res, x


def make_score_v2(with_cues: bool, head=None):
    def score(p, cues=None):
        scores = np.zeros(p["n_frames"] + 1)
        rules = {}
        last_f, last_s = 0, 0
        for f, res, _ in v2_frames(p, cues if with_cues else None, head):
            scores[last_f + 1:f] = last_s        # operator sees last score until next inference
            scores[f] = res["score"]
            last_f, last_s = f, res["score"]
            for s in res["signals"]:
                rules[s["rule"]] = rules.get(s["rule"], 0) + 1
        scores[last_f + 1:] = last_s
        return scores, rules
    return score


# ── metrics ──────────────────────────────────────────────────────────────

def roc_auc(y: np.ndarray, s: np.ndarray):
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    allv = np.concatenate([pos, neg])[order]
    # average ranks for ties
    i = 0
    r = np.empty(len(allv))
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        r[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    ranks[order] = r
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def threshold_metrics(y, s, thr):
    pred = s >= thr
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum()); tn = int((~pred & (y == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else 0.0
    return {"threshold": thr, "precision": _r(prec), "recall": _r(rec), "f1": _r(f1),
            "fpr": _r(fp / (fp + tn) if fp + tn else None), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def false_alarms_per_minute(per_video, thr):
    """Rising edges of (score ≥ thr) that start in a normal frame, per minute of normal footage."""
    alarms, normal_s = 0, 0.0
    for y, s, fps in per_video:
        on = s >= thr
        edges = np.where(on[1:] & ~on[:-1])[0] + 1
        alarms += int(sum(1 for e in edges if y[e] == 0))
        if on[0] and y[0] == 0:
            alarms += 1
        normal_s += (y == 0).sum() / fps
    return _r(alarms / (normal_s / 60) if normal_s else None), alarms, round(normal_s / 60, 2)


def _r(x, k=4):
    return None if x is None else round(float(x), k)


def eval_atmav(variant, kind, scorer, split, device, infer_fps):
    from data.labels import ANNOTATIONS
    man = json.load(open(ATMAV_MANIFEST))
    vids = [v for v in man["videos"] if v["split"] == split]
    ys, ss, per_video, video_rows, rule_totals = [], [], [], [], {}
    for v in vids:
        path = os.path.join(REPO_ROOT, "data", "videos", v["video"])
        p = run_pass(kind, "atmav", v["video"], path, infer_fps, device)
        cues = motion_pass("atmav", v["video"], path, infer_fps) if kind != "v0" else None
        scores, rules = scorer(p, cues)
        for k, n in rules.items():
            rule_totals[k] = rule_totals.get(k, 0) + n
        n = p["n_frames"]
        y = np.zeros(n + 1, dtype=int)
        for a, b in ANNOTATIONS[v["video"]][2]:
            y[a:min(b, n) + 1] = 1
        y, s = y[1:], scores[1:]
        ys.append(y); ss.append(s); per_video.append((y, s, p["fps"]))
        anomalous = bool(y.any())
        video_rows.append({"video": v["video"], "anomalous": anomalous, "frames": int(n),
                           "max_score": int(s.max()) if len(s) else 0,
                           "alarm_in_anomaly": bool((s[y == 1] >= OPEN_SCORE).any()) if anomalous else None,
                           "alarm_in_normal": bool((s[y == 0] >= OPEN_SCORE).any()),
                           "frame_auc": _r(roc_auc(y, s))})
    y, s = np.concatenate(ys), np.concatenate(ss)
    an = [r for r in video_rows if r["anomalous"]]
    fa_open = false_alarms_per_minute(per_video, OPEN_SCORE)
    fa_high = false_alarms_per_minute(per_video, HIGH_SCORE)
    return {
        "split": split, "videos": len(vids), "anomalous_videos": len(an), "frames": int(len(y)),
        "anomalous_frames": int(y.sum()),
        "frame_auc": _r(roc_auc(y, s)),
        "at_incident_threshold": threshold_metrics(y, s, OPEN_SCORE),
        "at_high": threshold_metrics(y, s, HIGH_SCORE),
        "video_detection_rate": _r(np.mean([r["alarm_in_anomaly"] for r in an])) if an else None,
        "videos_with_false_alarm": _r(np.mean([r["alarm_in_normal"] for r in video_rows])),
        "false_alarms_per_min_normal": {"incident": fa_open[0], "high": fa_high[0],
                                        "events_incident": fa_open[1], "normal_minutes": fa_open[2]},
        "rule_activations": rule_totals, "per_video": video_rows,
    }


def eval_scfd(variant, kind, scorer, split, device, infer_fps):
    man = json.load(open(SCFD_MANIFEST))
    clips = [c for c in man["clips"] if c["split"] == split]
    labels, peak, mean = [], [], []
    rule_by_label = {0: {}, 1: {}}
    for c in clips:
        path = os.path.join(SCFD_ROOT, c["clip"])
        p = run_pass(kind, "scfd", c["clip"], path, infer_fps, device)
        cues = motion_pass("scfd", c["clip"], path, infer_fps) if kind != "v0" else None
        scores, rules = scorer(p, cues)
        s = scores[1:]
        labels.append(c["label"]); peak.append(s.max() if len(s) else 0); mean.append(s.mean() if len(s) else 0)
        for k in rules:
            rule_by_label[c["label"]][k] = rule_by_label[c["label"]].get(k, 0) + 1
    y, pk = np.array(labels), np.array(peak)
    nf, nn = int(y.sum()), int((1 - y).sum())
    return {
        "split": split, "clips": len(clips), "fight": nf, "noFight": nn,
        "clip_auc_peak_score": _r(roc_auc(y, pk)), "clip_auc_mean_score": _r(roc_auc(y, np.array(mean))),
        "at_incident_threshold": threshold_metrics(y, pk, OPEN_SCORE),
        "rule_fired_share": {
            "fight": {k: _r(v / nf) for k, v in sorted(rule_by_label[1].items())},
            "noFight": {k: _r(v / nn) for k, v in sorted(rule_by_label[0].items())}},
    }


def _variants():
    from core.temporal import TemporalHead
    out = {"v0": ("v0", score_v0)}
    for short, model_id in (("coco", "yolov8n-coco"), ("sentinelx", "sentinelx-det-v1")):
        out[f"v2-{short}"] = (model_id, make_score_v2(True))
        head = TemporalHead.for_model(model_id)
        if head is not None:
            out[f"v2-{short}+temporal"] = (model_id, make_score_v2(True, head))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variants", nargs="+", default=None)
    p.add_argument("--splits", nargs="+", default=["test"])
    p.add_argument("--device", default=None)
    p.add_argument("--infer-fps", type=float, default=None)
    p.add_argument("--out", default=OUT)
    args = p.parse_args(argv)
    import config
    from core.detector import pick_device
    from core.threat_engine import ENGINE_VERSION
    device = args.device or pick_device()
    infer_fps = args.infer_fps or config.INFER_FPS
    report = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "protocol": {"incident_threshold": OPEN_SCORE, "high_threshold": HIGH_SCORE,
                           "temporal_heads": "trained on ATMA-V train split, selected on val (ml/train_temporal.py)",
                           "v2_infer_fps": infer_fps, "v2_engine": ENGINE_VERSION, "device": device,
                           "zones": "default ATM zone (5%-95% of frame) for every video — no per-video tuning"},
              "variants": {}}
    if os.path.exists(os.path.join(args.out, "report.json")):
        prev = json.load(open(os.path.join(args.out, "report.json")))
        report["variants"].update(prev.get("variants", {}))
    variants = _variants()
    for name in args.variants or list(variants):
        if name not in variants:
            raise SystemExit(f"unknown variant {name} (available: {list(variants)})")
        kind, scorer = variants[name]
        t0 = time.time()
        res = report["variants"].get(name) or {}
        res.update({"detector": kind if kind != "v0" else "yolov8n-coco (v0 settings)"})
        res.setdefault("atmav", {}); res.setdefault("scfd", {})
        for split in args.splits:
            res["atmav"][split] = eval_atmav(name, kind, scorer, split, device, infer_fps)
            res["scfd"][split] = eval_scfd(name, kind, scorer, split, device, infer_fps)
        res["wall_time_s"] = round(time.time() - t0)
        report["variants"][name] = res
        a = res["atmav"][args.splits[-1]]
        print(f"{name}: ATMA-V AUC={a['frame_auc']} P/R@50={a['at_incident_threshold']['precision']}/"
              f"{a['at_incident_threshold']['recall']} FPR={a['at_incident_threshold']['fpr']} "
              f"FA/min={a['false_alarms_per_min_normal']['incident']} | SCFD AUC={res['scfd'][args.splits[-1]]['clip_auc_peak_score']}")
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    write_markdown(report, os.path.join(args.out, "README.md"))


def write_markdown(rep, path):
    L = ["# Threat-pipeline evaluation (video-level labels)", "",
         f"Generated {rep['generated_utc']} by `ml/evaluate_pipeline.py`. Incident threshold = score ≥ "
         f"{rep['protocol']['incident_threshold']}, HIGH = ≥ {rep['protocol']['high_threshold']}. "
         f"{rep['protocol']['zones']}. All values are computed by the script.", ""]
    for split in sorted({s for v in rep["variants"].values() for s in v["atmav"]}):
        L += [f"## ATMA-V — `{split}` split (frame-level temporal labels)", "",
              "| Variant | Frame AUC | P @50 | R @50 | F1 @50 | FPR @50 | P @75 | R @75 | FPR @75 | Video detection rate | Videos w/ false alarm | False alarms / min normal |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, v in rep["variants"].items():
            a = v["atmav"].get(split)
            if not a:
                continue
            i, h = a["at_incident_threshold"], a["at_high"]
            L.append(f"| `{name}` | {a['frame_auc']} | {i['precision']} | {i['recall']} | {i['f1']} | {i['fpr']} | "
                     f"{h['precision']} | {h['recall']} | {h['fpr']} | {a['video_detection_rate']} | "
                     f"{a['videos_with_false_alarm']} | {a['false_alarms_per_min_normal']['incident']} |")
        any_a = next(v["atmav"][split] for v in rep["variants"].values() if split in v["atmav"])
        L += ["", f"{any_a['videos']} videos ({any_a['anomalous_videos']} with anomalies), {any_a['frames']} frames "
              f"({any_a['anomalous_frames']} anomalous).", ""]
        L += [f"## SCFD — `{split}` split (clip-level fight / noFight)", "",
              "| Variant | Clip AUC (peak score) | Clip AUC (mean score) | P @50 | R @50 | FPR @50 |", "|---|---|---|---|---|---|"]
        for name, v in rep["variants"].items():
            c = v["scfd"].get(split)
            if not c:
                continue
            i = c["at_incident_threshold"]
            L.append(f"| `{name}` | {c['clip_auc_peak_score']} | {c['clip_auc_mean_score']} | {i['precision']} | "
                     f"{i['recall']} | {i['fpr']} |")
        L.append("")
    L += ["## Rule activations (ATMA-V, number of evaluated frames where the rule fired)", ""]
    for name, v in rep["variants"].items():
        for split, a in v["atmav"].items():
            if a["rule_activations"]:
                L.append(f"- `{name}` ({split}): " + ", ".join(f"{k}={n}" for k, n in sorted(a["rule_activations"].items())))
    L += ["", "v0 has no structured rule output (its reasons are free text), so no activation counts are shown for it.", ""]
    with open(path, "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
