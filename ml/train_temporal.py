"""
Train the learned temporal anomaly head for one detector.

  python -m ml.train_temporal --model yolov8n-coco
  python -m ml.train_temporal --model sentinelx-det-v1

Data: ATMA-V frame-level temporal labels.
  * fit on the `train` split, choose L2 strength + decision threshold on `val`
  * the `test` split is NEVER read here (it is reported only by
    ml/evaluate_pipeline.py)
Features: core/temporal.py (window mean/max of tracker, object and OpenCV
motion cues) computed from the same cached detections the evaluator uses.
Model: L2-regularised logistic regression fitted by Newton/IRLS, with sample
weights that give every video equal total weight and balance the classes.

Writes models/temporal/<model>.json (used live by the engine) and
reports/temporal/<model>.json (training/validation metrics, coefficients).
"""

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np

from ml.classes import REPO_ROOT
from ml.evaluate_pipeline import ATMAV_MANIFEST, motion_pass, roc_auc, run_pass, v2_frames

C_GRID = [0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]


def collect(model_id: str, split: str, infer_fps: float, device: str):
    from data.labels import ANNOTATIONS
    man = json.load(open(ATMAV_MANIFEST))
    X, y, vid = [], [], []
    for i, v in enumerate(x for x in man["videos"] if x["split"] == split):
        path = os.path.join(REPO_ROOT, "data", "videos", v["video"])
        p = run_pass(model_id, "atmav", v["video"], path, infer_fps, device)
        cues = motion_pass("atmav", v["video"], path, infer_fps)
        segs = ANNOTATIONS[v["video"]][2]
        for f, _res, x in v2_frames(p, cues, head=None, collect=True):
            X.append(x)
            y.append(int(any(a <= f <= b for a, b in segs)))
            vid.append(i)
    return np.array(X), np.array(y), np.array(vid)


def sample_weights(y, vid):
    w = np.zeros(len(y))
    for v in np.unique(vid):
        m = vid == v
        w[m] = 1.0 / m.sum()                 # each video contributes equally
    for c in (0, 1):                          # then balance classes
        m = y == c
        if m.any():
            w[m] *= 0.5 / w[m].sum()
    return w * len(y)


def fit_logreg(X, y, w, C, iters=50):
    """Newton/IRLS for weighted L2 logistic regression on standardised X."""
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    beta = np.zeros(d + 1)
    reg = np.full(d + 1, 1.0 / C)
    reg[-1] = 0.0                            # no penalty on the intercept
    for _ in range(iters):
        z = np.clip(Xb @ beta, -40, 40)
        p = 1 / (1 + np.exp(-z))
        g = Xb.T @ (w * (p - y)) + reg * beta
        H = (Xb * (w * p * (1 - p))[:, None]).T @ Xb + np.diag(reg) + 1e-9 * np.eye(d + 1)
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-7:
            break
    return beta[:-1], beta[-1]


def best_threshold(y, p):
    """Threshold maximising Youden's J (TPR - FPR) on validation frames.
    F1 is not used: with mostly-anomalous validation frames it rewards
    flagging everything, which is useless for an alarm system."""
    best = (0.5, -1.0)
    pos, neg = max((y == 1).sum(), 1), max((y == 0).sum(), 1)
    for t in np.unique(np.round(p, 3)):
        pred = p >= t
        j = (pred & (y == 1)).sum() / pos - (pred & (y == 0)).sum() / neg
        if j > best[1]:
            best = (float(t), float(j))
    return best


def feature_groups(names):
    """A-priori feature groups compared on validation (no free feature search)."""
    image = {"motion", "motion_zone", "brightness", "sharpness", "brightness_drop", "sharpness_drop", "no_person_motion"}
    stem = lambda n: n.split("_", 1)[1]  # noqa: E731
    return {
        "all": np.ones(len(names), bool),
        "image_cues": np.array([stem(n) in image for n in names]),
        "behaviour": np.array([stem(n) not in image for n in names]),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True)
    p.add_argument("--device", default="cpu")
    p.add_argument("--infer-fps", type=float, default=10.0)
    args = p.parse_args(argv)
    from core.temporal import WINDOW_SECONDS, TemporalFeatures

    Xtr, ytr, vtr = collect(args.model, "train", args.infer_fps, args.device)
    Xva, yva, vva = collect(args.model, "val", args.infer_fps, args.device)
    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Ztr, Zva = (Xtr - mu) / sd, (Xva - mu) / sd
    wtr = sample_weights(ytr, vtr)

    names = TemporalFeatures.names()
    trials = []
    for group, mask in feature_groups(names).items():
        for C in C_GRID:
            coef_sub, b = fit_logreg(Ztr[:, mask], ytr, wtr, C)
            coef = np.zeros(len(names))
            coef[mask] = coef_sub
            pva = 1 / (1 + np.exp(-np.clip(Zva @ coef + b, -40, 40)))
            ptr = 1 / (1 + np.exp(-np.clip(Ztr @ coef + b, -40, 40)))
            trials.append({"group": group, "C": C, "train_auc": roc_auc(ytr, ptr), "val_auc": roc_auc(yva, pva),
                           "coef": coef, "b": b, "pva": pva})
            print(f"{group:10s} C={C}: train AUC {trials[-1]['train_auc']:.4f}  val AUC {trials[-1]['val_auc']:.4f}")
    best = max(trials, key=lambda t: t["val_auc"])
    thr, youden = best_threshold(yva, best["pva"])
    spec = {
        "version": f"temporal-logreg-v1/{args.model}",
        "detector": args.model, "features": names, "window_seconds": WINDOW_SECONDS,
        "mean": mu.tolist(), "std": sd.tolist(), "coef": best["coef"].tolist(), "intercept": float(best["b"]),
        "threshold": thr,
    }
    os.makedirs(os.path.join(REPO_ROOT, "models", "temporal"), exist_ok=True)
    with open(os.path.join(REPO_ROOT, "models", "temporal", f"{args.model}.json"), "w") as f:
        json.dump(spec, f, indent=1)
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detector": args.model, "dataset": "ATMA-V (data/manifests/atmav/manifest.json)",
        "train": {"videos": int(len(np.unique(vtr))), "frames": int(len(ytr)), "anomalous_frames": int(ytr.sum())},
        "val": {"videos": int(len(np.unique(vva))), "frames": int(len(yva)), "anomalous_frames": int(yva.sum())},
        "selection": [{"features": t["group"], "C": t["C"], "train_auc": round(t["train_auc"], 4),
                       "val_auc": round(t["val_auc"], 4)} for t in trials],
        "chosen": {"features": best["group"], "C": best["C"], "val_auc": round(best["val_auc"], 4),
                   "threshold": thr, "val_youden_j": round(youden, 4),
                   "threshold_rule": "max Youden J (TPR - FPR) on val"},
        "top_coefficients": sorted(({"feature": n, "coef": round(float(c), 4)} for n, c in zip(names, best["coef"])),
                                   key=lambda r: -abs(r["coef"]))[:15],
        "note": "test split not used; see reports/pipeline for held-out results",
    }
    os.makedirs(os.path.join(REPO_ROOT, "reports", "temporal"), exist_ok=True)
    with open(os.path.join(REPO_ROOT, "reports", "temporal", f"{args.model}.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report["chosen"]), "\ntop features:", [(r["feature"], r["coef"]) for r in report["top_coefficients"][:8]])


if __name__ == "__main__":
    main()
