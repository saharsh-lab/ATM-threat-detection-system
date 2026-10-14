# web/registry.py
# Loads the committed dataset/model registries and evaluation reports, seeds
# them into the database (so incidents can reference model_version /
# dataset_id), and exports a static JSON snapshot for the serverless demo.

import json
import logging
import os

import yaml

import config
from core import repository

log = logging.getLogger("sentinelx.registry")

DATASETS_YAML = os.path.join(config.BASE_DIR, "data", "registry", "datasets.yaml")
REPORTS = {
    "detector": os.path.join(config.BASE_DIR, "reports", "detector", "report.json"),
    "pipeline": os.path.join(config.BASE_DIR, "reports", "pipeline", "report.json"),
    "training": os.path.join(config.BASE_DIR, "reports", "training", "sentinelx-det-v1", "train_meta.json"),
    "temporal_coco": os.path.join(config.BASE_DIR, "reports", "temporal", "yolov8n-coco.json"),
    "temporal_sentinelx": os.path.join(config.BASE_DIR, "reports", "temporal", "sentinelx-det-v1.json"),
}
STATIC_EXPORT = os.path.join(config.BASE_DIR, "static", "data", "platform.json")


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def datasets() -> list[dict]:
    with open(DATASETS_YAML) as f:
        items = yaml.safe_load(f)["datasets"]
    for d in items:
        mp = d.get("manifest_path") or ""
        summary = os.path.join(config.BASE_DIR, mp, "summary.json") if mp.endswith("/") else None
        manifest = os.path.join(config.BASE_DIR, mp) if mp.endswith(".json") else None
        s = _read_json(summary) if summary else None
        if s:
            d["version"] = s.get("version")
            d["stats"] = {k: v["images"] for k, v in s["stats"].get("splits", {}).items()}
        m = _read_json(manifest) if manifest else None
        if m:
            d["stats"] = m.get("split_counts") or m.get("counts")
    return items


def models() -> list[dict]:
    from core.detector import load_registry
    out = []
    det = _read_json(REPORTS["detector"]) or {}
    for m in load_registry():
        entry = dict(m)
        entry["weights_present"] = os.path.exists(os.path.join(config.BASE_DIR, m["weights"]))
        ev = det.get("models", {}).get(m["id"])
        if ev:
            entry["evaluation"] = {k: ev.get(k) for k in ("map50_shared", "map50_95_shared", "map50_all_classes",
                                                           "map50_95_all_classes", "latency")}
        out.append(entry)
    return out


def reports() -> dict:
    return {k: _read_json(p) for k, p in REPORTS.items()}


def seed(db_path: str | None = None):
    """Idempotently mirror the registries into the database."""
    try:
        for d in datasets():
            repository.upsert_dataset({
                "id": d["id"], "name": d["name"], "version": d.get("version"),
                "label_type": d["label_type"], "source_url": d.get("source_url"), "license": d.get("license"),
                "access": d.get("status"), "manifest_path": d.get("manifest_path"),
                "stats": d.get("stats") or {}}, db=db_path)
        for m in models():
            repository.upsert_model({
                "id": m["id"], "name": m["name"], "weights_path": m["weights"],
                "weights_sha256": m.get("sha256"), "class_schema": m["schema"],
                "dataset_id": m.get("dataset_id"), "engine_version": None,
                "metrics": m.get("evaluation") or {}}, db=db_path)
    except Exception:   # noqa: BLE001 — registry problems must not stop the platform
        log.exception("registry seeding failed")


def export_static(path: str = STATIC_EXPORT):
    """Snapshot for the serverless demo and the Model Lab figures: registry +
    report JSON to static/data/platform.json, report PNGs to static/reports/."""
    import shutil
    data = {"datasets": datasets(), "models": models(), "reports": reports()}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=1, default=str)
    src_root = os.path.join(config.BASE_DIR, "reports")
    dst_root = os.path.join(config.BASE_DIR, "static", "reports")
    for group in ("detector", "pipeline", os.path.join("training", "sentinelx-det-v1")):
        src = os.path.join(src_root, group)
        if not os.path.isdir(src):
            continue
        os.makedirs(os.path.join(dst_root, group), exist_ok=True)
        for f in os.listdir(src):
            if f.endswith(".png"):
                shutil.copy2(os.path.join(src, f), os.path.join(dst_root, group, f))
    return path
