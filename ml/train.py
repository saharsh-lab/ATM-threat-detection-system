"""
Fine-tune a SentinelX detector.

  python -m ml.train --config ml/configs/train_v1.yaml [--device mps] [--epochs N]

Outputs:
  runs/train/<run_id>/            Ultralytics run directory (git-ignored)
  models/<run_id>.pt              best checkpoint (committed; small)
  reports/training/<run_id>/      args, per-epoch results.csv, curves, metadata
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import yaml

from ml.classes import REPO_ROOT


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:   # noqa: BLE001
        return "unknown"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="ml/configs/train_v1.yaml")
    p.add_argument("--device", default=None)
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args(argv)

    from ultralytics import YOLO
    from core.detector import pick_device, sha256_file

    with open(os.path.join(REPO_ROOT, args.config)) as f:
        cfg = yaml.safe_load(f)
    run_id = cfg["run_id"]
    data_yaml = os.path.join(REPO_ROOT, cfg["data_yaml"])
    if not os.path.exists(data_yaml):
        sys.exit(f"dataset not built: {data_yaml}\nrun: python -m ml.datasets.openimages reproduce")
    with open(os.path.join(REPO_ROOT, "data", "manifests", cfg["dataset"], "summary.json")) as f:
        ds_summary = json.load(f)

    train_args = dict(cfg["train"])
    if args.epochs:
        train_args["epochs"] = args.epochs
    device = args.device or pick_device()
    project = os.path.join(REPO_ROOT, "runs", "train")
    run_dir = os.path.join(project, run_id)

    t0 = time.time()
    if args.resume:
        model = YOLO(os.path.join(run_dir, "weights", "last.pt"))
        model.train(resume=True)
    else:
        model = YOLO(os.path.join(REPO_ROOT, cfg["base_weights"]))
        model.train(data=data_yaml, project=project, name=run_id, exist_ok=True, device=device, **train_args)
    duration = time.time() - t0

    best = os.path.join(run_dir, "weights", "best.pt")
    dest = os.path.join(REPO_ROOT, "models", f"{run_id}.pt")
    shutil.copy2(best, dest)

    report_dir = os.path.join(REPO_ROOT, "reports", "training", run_id)
    os.makedirs(report_dir, exist_ok=True)
    for name in ("args.yaml", "results.csv", "results.png", "confusion_matrix_normalized.png",
                 "BoxPR_curve.png", "PR_curve.png"):
        src = os.path.join(run_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(report_dir, name))
    meta = {
        "run_id": run_id,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": cfg,
        "effective_train_args": {**train_args, "device": device},
        "dataset": {"id": cfg["dataset"], "version": ds_summary["version"],
                    "splits": {k: v["images"] for k, v in ds_summary["stats"]["splits"].items()}},
        "base_weights_sha256": sha256_file(os.path.join(REPO_ROOT, cfg["base_weights"])),
        "weights": os.path.relpath(dest, REPO_ROOT),
        "weights_sha256": sha256_file(dest),
        "weights_bytes": os.path.getsize(dest),
        "wall_time_seconds": round(duration),
    }
    with open(os.path.join(report_dir, "train_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
