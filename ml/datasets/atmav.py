"""
ATMA-V (ATM Anomaly Video Dataset) — validation, checksums and leakage-safe
video-level splits.

Source:  https://www.kaggle.com/datasets/mehantkammakomati/atm-anomaly-video-dataset-atmav
License: CC BY-SA 4.0 as declared on Kaggle by the uploader. The footage was
         collected from public internet sources (YouTube, stock sites, ...), so
         upstream rights are not guaranteed by that declaration; see
         docs/DATASETS.md.

ATMA-V carries *video-level temporal* anomaly labels (frame ranges), not
bounding boxes. It is kept separate from the detection dataset and is only
used to calibrate and evaluate the end-to-end threat pipeline.

Usage:
  python -m ml.datasets.atmav            # validate + write data/manifests/atmav/
  python -m ml.datasets.atmav --download # fetch from Kaggle (needs Kaggle API token)
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys

import cv2

from ml.classes import REPO_ROOT

log = logging.getLogger("sentinelx.atmav")

KAGGLE_REF = "mehantkammakomati/atm-anomaly-video-dataset-atmav"
VIDEO_DIR = os.path.join(REPO_ROOT, "data", "videos")
MANIFEST_DIR = os.path.join(REPO_ROOT, "data", "manifests", "atmav")
SPLIT_FRACTIONS = {"train": 0.5, "val": 0.2, "test": 0.3}
SEED = 7


def source_group(filename: str) -> str:
    """Clips cut from the same source recording share a group ('30a','30b' -> '30')."""
    m = re.match(r"(\d+)", filename)
    return m.group(1) if m else filename


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_video(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"readable": False}
    info = {
        "readable": True,
        "fps": round(cap.get(cv2.CAP_PROP_FPS) or 0.0, 3),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "frames_reported": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    # Container frame counts are unreliable for some encodes; count decoded frames.
    n = 0
    while cap.grab():
        n += 1
    cap.release()
    info["frames_decoded"] = n
    return info


def validate_annotations(annotations: dict, probes: dict) -> tuple[list[str], list[str]]:
    """Return (errors, notes). Errors make a label unusable; notes are benign
    (e.g. a container that over-reports its frame count)."""
    errors, notes = [], []
    for name, (total, fps, segments) in sorted(annotations.items()):
        p = probes.get(name)
        if p is None:
            errors.append(f"{name}: labelled but video file missing")
            continue
        if not p["readable"]:
            errors.append(f"{name}: video not decodable")
            continue
        if total != p["frames_reported"]:
            errors.append(f"{name}: label total_frames={total} != container {p['frames_reported']}")
        if p["frames_decoded"] != p["frames_reported"]:
            notes.append(f"{name}: container reports {p['frames_reported']} frames, "
                         f"{p['frames_decoded']} decodable")
        prev_end = 0
        for start, end in segments:
            if start < 1 or end < start:
                errors.append(f"{name}: invalid segment ({start}, {end})")
            if end > p["frames_decoded"]:
                errors.append(f"{name}: segment ({start}, {end}) ends after last decodable "
                              f"frame {p['frames_decoded']}")
            if start <= prev_end:
                errors.append(f"{name}: overlapping/unsorted segment ({start}, {end})")
            prev_end = end
    for name in sorted(set(probes) - set(annotations)):
        errors.append(f"{name}: video has no label entry")
    return errors, notes


def anomaly_fraction(total: int, segments: list) -> float:
    return sum(e - s + 1 for s, e in segments) / total if total else 0.0


def make_splits(annotations: dict) -> dict:
    """Group-aware split, stratified on whether the group contains anomalies."""
    groups = {}
    for name in annotations:
        groups.setdefault(source_group(name), []).append(name)
    anomalous = sorted(g for g, vids in groups.items() if any(annotations[v][2] for v in vids))
    normal = sorted(g for g in groups if g not in anomalous)
    rng = random.Random(SEED)
    split_of = {}
    for bucket in (anomalous, normal):
        rng.shuffle(bucket)
        n = len(bucket)
        n_test = round(n * SPLIT_FRACTIONS["test"])
        n_val = round(n * SPLIT_FRACTIONS["val"])
        for i, g in enumerate(bucket):
            split_of[g] = "test" if i < n_test else "val" if i < n_test + n_val else "train"
    return {name: split_of[source_group(name)] for name in annotations}


def download():
    """Download via the official Kaggle CLI. Requires ~/.kaggle/kaggle.json."""
    os.makedirs(VIDEO_DIR, exist_ok=True)
    tmp = os.path.join(REPO_ROOT, "datasets", "raw", "atmav")
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(["kaggle", "datasets", "download", "-d", KAGGLE_REF, "-p", tmp, "--unzip"], check=True)
    src = os.path.join(tmp, "ATMA-V Dataset", "videos")
    for f in os.listdir(src):
        if f.endswith(".mp4") and not os.path.exists(os.path.join(VIDEO_DIR, f)):
            os.replace(os.path.join(src, f), os.path.join(VIDEO_DIR, f))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--download", action="store_true", help="download from Kaggle first")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.download:
        download()

    from data.labels import ANNOTATIONS

    probes, checksums = {}, {}
    for f in sorted(os.listdir(VIDEO_DIR)):
        if f.endswith(".mp4"):
            path = os.path.join(VIDEO_DIR, f)
            probes[f] = probe_video(path)
            checksums[f] = sha256_file(path)
    errors, notes = validate_annotations(ANNOTATIONS, probes)
    splits = make_splits(ANNOTATIONS)

    os.makedirs(MANIFEST_DIR, exist_ok=True)
    videos = []
    for name in sorted(ANNOTATIONS, key=lambda n: (int(source_group(n)), n)):
        total, fps, segments = ANNOTATIONS[name]
        videos.append({
            "video": name, "group": source_group(name), "split": splits[name],
            "label_total_frames": total, "label_fps": fps, "segments": segments,
            "anomaly_fraction": round(anomaly_fraction(total, segments), 4),
            "sha256": checksums.get(name), **probes.get(name, {}),
        })
    summary = {
        "dataset": "ATMA-V",
        "source": f"https://www.kaggle.com/datasets/{KAGGLE_REF}",
        "license_declared": "CC BY-SA 4.0",
        "label_type": "video-level temporal frame ranges (1-indexed, inclusive)",
        "split_policy": "group-aware by source recording id, stratified by anomalous/normal group",
        "split_seed": SEED,
        "split_counts": {s: sum(1 for v in videos if v["split"] == s) for s in SPLIT_FRACTIONS},
        "validation_errors": errors,
        "validation_notes": notes,
        "videos": videos,
    }
    with open(os.path.join(MANIFEST_DIR, "manifest.json"), "w") as f:
        json.dump(summary, f, indent=2)
    log.info("ATMA-V: %d videos, splits %s, %d errors, %d notes",
             len(videos), summary["split_counts"], len(errors), len(notes))
    for e in errors:
        log.error("  %s", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
