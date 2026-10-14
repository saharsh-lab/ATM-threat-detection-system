"""
Build the SentinelX object-detection dataset from Open Images V7.

Source:   https://storage.googleapis.com/openimages/web/index.html (official)
Licenses: annotations CC BY 4.0 (Google LLC); images are listed as CC BY 2.0
          (Google makes no warranty, so every image's licence/author is recorded
          in the committed manifest).

Stages (each cached on disk, so re-running resumes where it stopped):

  fetch     stream the official CSVs, keep only rows for SentinelX classes
  select    clean annotations, sample images per class, leakage-safe split
  download  fetch selected images from the official CVDF S3 mirror, resize, verify
  build     near-duplicate removal, YOLO labels, data.yaml, committed manifests

  reproduce rebuild the exact dataset from the committed manifest
            (download + build only; no selection randomness)

Usage:
  python -m ml.datasets.openimages all
  python -m ml.datasets.openimages reproduce
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import cv2
import numpy as np
import pandas as pd
import requests

from ml.classes import REPO_ROOT, class_names, load_schema, openimages_mid_to_class

log = logging.getLogger("sentinelx.openimages")

DATASET_NAME = "openimages_v7_sentinelx_v1"
RAW_DIR = os.path.join(REPO_ROOT, "datasets", "raw", "openimages")
FILTERED_DIR = os.path.join(RAW_DIR, "filtered")
IMAGE_CACHE = os.path.join(RAW_DIR, "images")
OUT_DIR = os.path.join(REPO_ROOT, "datasets", "processed", DATASET_NAME)
MANIFEST_DIR = os.path.join(REPO_ROOT, "data", "manifests", DATASET_NAME)

GCS = "https://storage.googleapis.com/openimages"
SOURCES = {
    "train": {
        "bbox": f"{GCS}/v6/oidv6-train-annotations-bbox.csv",
        "labels": f"{GCS}/v7/oidv7-train-annotations-human-imagelabels.csv",
        "meta": f"{GCS}/2018_04/train/train-images-boxable-with-rotation.csv",
        "s3_folder": "train",
    },
    "validation": {
        "bbox": f"{GCS}/v5/validation-annotations-bbox.csv",
        "labels": f"{GCS}/v7/oidv7-val-annotations-human-imagelabels.csv",
        "meta": f"{GCS}/2018_04/validation/validation-images-with-rotation.csv",
        "s3_folder": "validation",
    },
    "test": {
        "bbox": f"{GCS}/v5/test-annotations-bbox.csv",
        "labels": f"{GCS}/v7/oidv7-test-annotations-human-imagelabels.csv",
        "meta": f"{GCS}/2018_04/test/test-images-with-rotation.csv",
        "s3_folder": "test",
    },
}
S3_IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{folder}/{image_id}.jpg"

# Split policy: SentinelX val/test come from the official OI validation+test
# subsets (disjoint images, denser human verification) partitioned by
# photographer; SentinelX train comes from the official OI train subset.

# Max images sampled per class (images containing the class). Rarer classes
# are sampled first so they are not crowded out.
CAPS = {
    "train": {"person": 2500, "knife": 1500, "firearm": 1500, "backpack": 1200,
              "handbag": 1200, "suitcase": 1000, "bottle": 1200, "helmet": 1200,
              "blunt_tool": 1000},
    # held-out caps are above the available pool for rare classes on purpose:
    # every eligible held-out image of a rare class is kept.
    "val":   {"person": 500, "knife": 400, "firearm": 400, "backpack": 400,
              "handbag": 400, "suitcase": 400, "bottle": 300, "helmet": 300,
              "blunt_tool": 400},
    "test":  {"person": 800, "knife": 600, "firearm": 600, "backpack": 600,
              "handbag": 600, "suitcase": 600, "bottle": 450, "helmet": 450,
              "blunt_tool": 600},
}

MAX_SIDE = 640           # images are stored at training resolution
MIN_BOX_SIDE = 0.002     # normalised; smaller boxes are degenerate
DEDUP_IOU = 0.85         # same-class boxes above this IoU are merged (e.g. Person+Man)
NEAR_DUP_HAMMING = 4     # dHash distance treated as a near-duplicate image
SEED = 42
TRAIN_SIDE_RATIO = 5.0      # see select(): photographer side assignment
TRAIN_SIDE_MIN_RARE = 20


# ── fetch ────────────────────────────────────────────────────────────────

def _stream_filter(url: str, out_path: str, keep, max_retries: int = 20) -> int:
    """Stream a remote CSV, writing the header plus rows where keep(row_bytes).
    Connection drops are resumed with HTTP Range from the last complete line."""
    if os.path.exists(out_path):
        log.info("cached %s", out_path)
        return -1
    tmp = out_path + ".part"
    kept = scanned = 0
    offset = 0            # bytes of the remote file fully consumed (complete lines)
    header_done = False
    t0 = time.time()
    with open(tmp, "wb") as out:
        for attempt in range(max_retries + 1):
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with requests.get(url, stream=True, timeout=60, headers=headers) as r:
                    r.raise_for_status()
                    if offset and r.status_code != 206:
                        raise RuntimeError(f"server ignored Range request for {url}")
                    buf = b""
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        for line in lines:
                            offset += len(line) + 1
                            line = line.rstrip(b"\r")
                            if not header_done:
                                out.write(line + b"\n")
                                header_done = True
                                continue
                            scanned += 1
                            if line and keep(line):
                                out.write(line + b"\n")
                                kept += 1
                            if scanned % 5_000_000 == 0:
                                log.info("  %s: %dM rows scanned, %d kept (%.0fs)",
                                         os.path.basename(out_path), scanned // 1_000_000,
                                         kept, time.time() - t0)
                    if buf.strip():
                        scanned += 1
                        if keep(buf.rstrip(b"\r")):
                            out.write(buf.rstrip(b"\r") + b"\n")
                            kept += 1
                break
            except (requests.RequestException, ConnectionError) as e:
                if attempt == max_retries:
                    raise
                log.warning("stream interrupted at byte %d (%s); resuming", offset, e)
                time.sleep(min(30, 2 ** attempt))
    os.replace(tmp, out_path)
    log.info("wrote %s (%d of %d rows kept, %.0fs)", out_path, kept, scanned, time.time() - t0)
    return kept


def fetch():
    os.makedirs(FILTERED_DIR, exist_ok=True)
    mids = {m.encode() for m in openimages_mid_to_class()}

    def label_filter(line: bytes) -> bool:
        parts = line.split(b",", 3)
        return len(parts) > 2 and parts[2] in mids

    for subset, src in SOURCES.items():
        bbox_path = os.path.join(FILTERED_DIR, f"{subset}-bbox.csv")
        _stream_filter(src["bbox"], bbox_path, label_filter)
        _stream_filter(src["labels"], os.path.join(FILTERED_DIR, f"{subset}-imagelabels.csv"),
                       label_filter)
        ids = set(pd.read_csv(bbox_path, usecols=["ImageID"])["ImageID"].astype(str))
        id_bytes = {i.encode() for i in ids}
        _stream_filter(src["meta"], os.path.join(FILTERED_DIR, f"{subset}-meta.csv"),
                       lambda line: line.split(b",", 1)[0] in id_bytes)


# ── select ───────────────────────────────────────────────────────────────

def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def clean_boxes(rows: pd.DataFrame) -> list[tuple]:
    """Clip, drop degenerate boxes and merge same-class duplicates.
    Returns [(cls, x1, y1, x2, y2)] in normalised xyxy."""
    boxes = []
    for r in rows.itertuples(index=False):
        x1, x2 = sorted((min(max(r.XMin, 0.0), 1.0), min(max(r.XMax, 0.0), 1.0)))
        y1, y2 = sorted((min(max(r.YMin, 0.0), 1.0), min(max(r.YMax, 0.0), 1.0)))
        if x2 - x1 < MIN_BOX_SIDE or y2 - y1 < MIN_BOX_SIDE:
            continue
        cand = (int(r.cls), x1, y1, x2, y2)
        if any(b[0] == cand[0] and _iou(b[1:], cand[1:]) > DEDUP_IOU for b in boxes):
            continue
        boxes.append(cand)
    return boxes


def _load_subset(subset: str, stats: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Apply image-level cleaning rules. Returns (bbox rows of eligible images,
    {image_id: set(class ids)}, metadata indexed by ImageID)."""
    mid2cls = openimages_mid_to_class()
    bbox = pd.read_csv(os.path.join(FILTERED_DIR, f"{subset}-bbox.csv"), dtype={"ImageID": str})
    labels = pd.read_csv(os.path.join(FILTERED_DIR, f"{subset}-imagelabels.csv"), dtype={"ImageID": str})
    meta = pd.read_csv(os.path.join(FILTERED_DIR, f"{subset}-meta.csv"), dtype={"ImageID": str})
    bbox["cls"] = bbox["LabelName"].map(mid2cls)
    labels["cls"] = labels["LabelName"].map(mid2cls)
    s = stats.setdefault(subset, {})
    all_ids = set(bbox["ImageID"])
    s["candidate_images"] = len(all_ids)

    bad = set(bbox.loc[(bbox["IsDepiction"] == 1) | (bbox["IsGroupOf"] == 1), "ImageID"])
    s["dropped_depiction_or_groupof"] = len(bad)

    meta = meta.drop_duplicates("ImageID").set_index("ImageID")
    rotated = set(meta.index[meta["Rotation"].fillna(-1) != 0]) & all_ids
    s["dropped_rotation_unknown_or_nonzero"] = len(rotated - bad)
    cc_by = meta["License"].astype(str).str.contains("creativecommons.org/licenses/by/2.0", regex=False)
    unlicensed = set(meta.index[~cc_by]) & all_ids
    s["dropped_license_not_cc_by_2"] = len(unlicensed - bad - rotated)
    missing_meta = all_ids - set(meta.index)
    s["dropped_missing_metadata"] = len(missing_meta)

    # Image-level consistency: a class that annotators verified as present
    # (Confidence=1) must have at least one box, otherwise the image contains
    # unboxed objects that would be learnt as background.
    boxed = bbox[["ImageID", "cls"]].drop_duplicates()
    verified = labels.loc[(labels["Confidence"] == 1) & labels["ImageID"].isin(all_ids),
                          ["ImageID", "cls"]].drop_duplicates()
    joined = verified.merge(boxed, how="left", on=["ImageID", "cls"], indicator=True)
    incomplete = set(joined.loc[joined["_merge"] == "left_only", "ImageID"])
    s["dropped_incomplete_boxes"] = len(incomplete - bad - rotated - unlicensed)

    drop = bad | rotated | unlicensed | missing_meta | incomplete
    bbox = bbox[~bbox["ImageID"].isin(drop)]
    boxed = boxed[~boxed["ImageID"].isin(drop)]
    img_classes = boxed.groupby("ImageID")["cls"].agg(set).to_dict()
    s["eligible_images"] = len(img_classes)
    return bbox, img_classes, meta


def _sample(img_classes: dict, caps: dict, rng: random.Random, exclude: set) -> list[str]:
    names = class_names()
    by_class = {c: [] for c in range(len(names))}
    for image_id, classes in img_classes.items():
        if image_id in exclude:
            continue
        for c in classes:
            by_class[c].append(image_id)
    chosen, seen = [], set()
    have = {c: 0 for c in by_class}
    for c in sorted(by_class, key=lambda k: len(by_class[k])):
        pool = sorted(by_class[c])
        rng.shuffle(pool)
        for image_id in pool:
            if have[c] >= caps[names[c]]:
                break
            if image_id in seen:
                continue
            chosen.append(image_id)
            seen.add(image_id)
            for k in img_classes[image_id]:
                have[k] += 1
    return chosen


def _record(image_id, split, subset, m, boxes):
    return {"image_id": image_id, "split": split, "oi_subset": subset,
            "license": m["License"], "author": m["Author"], "author_profile_url": m["AuthorProfileURL"],
            "landing_url": m["OriginalLandingURL"], "original_md5": m["OriginalMD5"], "boxes": boxes}


def _author_bucket(author: str) -> str:
    """Deterministic photographer -> held-out split assignment."""
    h = int(hashlib.sha256(f"{SEED}:{author}".encode()).hexdigest(), 16)
    return "val" if h % 5 < 2 else "test"            # 40% val / 60% test of held-out authors


def select():
    """Held-out splits are drawn first from the official (densely verified)
    validation+test subsets, partitioned by photographer. Train is then sampled
    from the official train subset excluding every held-out photographer and
    any exact-duplicate file (OriginalMD5)."""
    rng = random.Random(SEED)
    stats = {}
    selected = []

    # ── load all official subsets ────────────────────────────────────────
    held = {}
    for subset in ("validation", "test"):
        held[subset] = _load_subset(subset, stats)
    train_bbox, train_classes, train_meta = _load_subset("train", stats)

    # Each photographer is assigned to ONE side. Rare-class (non-person)
    # images are what both sides compete for, so a photographer goes to the
    # train side when they have more than TRAIN_SIDE_RATIO x as many rare-class
    # images in the train pool as in the held-out pool (and > TRAIN_SIDE_MIN_RARE);
    # otherwise held-out. This keeps a few very prolific weapon photographers
    # in train instead of letting them dominate (and leak into) the test set.
    def rare_counts(img_classes, meta):
        rare = [i for i, c in img_classes.items() if c - {0}]
        return meta["AuthorProfileURL"].reindex(rare).value_counts()
    rare_train = rare_counts(train_classes, train_meta)
    rare_held = sum((rare_counts(ic, m) for _, ic, m in held.values()), start=pd.Series(dtype=float))
    rare_held = rare_held.groupby(level=0).sum() if len(rare_held) else rare_held
    prolific = {a for a, n in rare_train.items()
                if n > TRAIN_SIDE_MIN_RARE and n > TRAIN_SIDE_RATIO * rare_held.get(a, 0)}
    stats["train_side_authors"] = len(prolific)

    held_authors, held_md5 = set(), set()
    for split in ("val", "test"):
        pool, origin = {}, {}
        for subset, (bbox, img_classes, meta) in held.items():
            authors = meta["AuthorProfileURL"].reindex(list(img_classes)).fillna("")
            for image_id, author in zip(img_classes, authors):
                if author in prolific:
                    continue
                if _author_bucket(author or image_id) == split:
                    pool[image_id] = img_classes[image_id]
                    origin[image_id] = subset
        chosen = _sample(pool, CAPS[split], rng, exclude=set())
        n = 0
        for image_id in chosen:
            subset = origin[image_id]
            bbox, _, meta = held[subset]
            boxes = clean_boxes(bbox[bbox["ImageID"] == image_id])
            if not boxes:
                continue
            m = meta.loc[image_id]
            selected.append(_record(image_id, split, subset, m, boxes))
            held_authors.add(m["AuthorProfileURL"])
            held_md5.add(m["OriginalMD5"])
            n += 1
        stats[f"{split}_selected"] = n
        log.info("%s: %d images from %d candidates", split, n, len(pool))

    # ── train: official train subset minus held-out photographers ─────────
    bbox, img_classes, meta = train_bbox, train_classes, train_meta
    ids = list(img_classes)
    authors = meta["AuthorProfileURL"].reindex(ids)
    md5 = meta["OriginalMD5"].reindex(ids)
    exclude = {i for i, a, m in zip(ids, authors, md5) if a in held_authors or m in held_md5}
    stats["train"]["dropped_author_or_md5_overlap_with_heldout"] = len(exclude)
    chosen = _sample(img_classes, CAPS["train"], rng, exclude)
    rows = bbox[bbox["ImageID"].isin(set(chosen))].groupby("ImageID")
    n = 0
    for image_id in chosen:
        boxes = clean_boxes(rows.get_group(image_id))
        if boxes:
            selected.append(_record(image_id, "train", "train", meta.loc[image_id], boxes))
            n += 1
    stats["train_selected"] = n
    log.info("train: %d images (excluded %d for photographer/MD5 overlap)", n, len(exclude))

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "selection.json"), "w") as f:
        json.dump({"stats": stats, "images": selected}, f)
    log.info("selected %d images", len(selected))


# ── download ─────────────────────────────────────────────────────────────

def _download_one(session: requests.Session, image_id: str, subset: str) -> tuple[str, str]:
    path = os.path.join(IMAGE_CACHE, f"{image_id}.jpg")
    if os.path.exists(path):
        return image_id, "cached"
    url = S3_IMAGE_URL.format(folder=SOURCES[subset]["s3_folder"], image_id=image_id)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404:
                return image_id, "missing"
            r.raise_for_status()
            img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
            if img is None or min(img.shape[:2]) < 32:
                return image_id, "invalid"
            h, w = img.shape[:2]
            scale = MAX_SIDE / max(h, w)
            if scale < 1:
                img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
            tmp = path + ".part.jpg"
            cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            os.replace(tmp, path)
            return image_id, "ok"
        except requests.RequestException:
            time.sleep(1 + attempt * 2)
    return image_id, "failed"


def download(selection: list[dict], workers: int = 32) -> dict:
    os.makedirs(IMAGE_CACHE, exist_ok=True)
    status = {}
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=workers, pool_maxsize=workers)
    session.mount("https://", adapter)
    t0 = time.time()
    with ThreadPoolExecutor(workers) as pool:
        futs = [pool.submit(_download_one, session, s["image_id"], s["oi_subset"]) for s in selection]
        for n, fut in enumerate(as_completed(futs), 1):
            image_id, st = fut.result()
            status[image_id] = st
            if n % 1000 == 0:
                log.info("  downloaded %d/%d (%.0fs)", n, len(futs), time.time() - t0)
    counts = {k: sum(1 for v in status.values() if v == k) for k in set(status.values())}
    log.info("download status: %s", counts)
    return status


# ── build ────────────────────────────────────────────────────────────────

def dhash(img: np.ndarray) -> int:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _hamming_min(query: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Min Hamming distance from each query hash to any ref hash (uint64 arrays)."""
    if len(ref) == 0:
        return np.full(len(query), 64)
    out = np.empty(len(query), dtype=np.int64)
    ref_bytes = ref.view(np.uint8).reshape(-1, 8)
    for i, q in enumerate(query):
        x = np.bitwise_xor(ref_bytes, np.array([q], dtype=np.uint64).view(np.uint8))
        out[i] = np.unpackbits(x, axis=1).sum(axis=1).min()
    return out


def build(selection: list[dict], status: dict, stats: dict):
    names = class_names()
    # Verify images and compute perceptual hashes
    kept = []
    for s in selection:
        if status.get(s["image_id"]) not in ("ok", "cached"):
            continue
        img = cv2.imread(os.path.join(IMAGE_CACHE, f"{s['image_id']}.jpg"))
        if img is None:
            continue
        s["height"], s["width"] = img.shape[:2]
        s["dhash"] = dhash(img)
        kept.append(s)
    stats["download_failures"] = len(selection) - len(kept)

    # Near-duplicate removal: within a split keep the first occurrence; across
    # splits, val/test images near-identical to train (or test to val) are dropped.
    final, ref = [], {"train": [], "val": []}
    dropped = {"within_split": 0, "cross_split": 0}
    for split in ("train", "val", "test"):
        items = [s for s in kept if s["split"] == split]
        hashes = np.array([s["dhash"] for s in items], dtype=np.uint64)
        cross_ref = np.array(ref["train"] + (ref["val"] if split == "test" else []), dtype=np.uint64)
        cross = _hamming_min(hashes, cross_ref) if split != "train" else np.full(len(items), 64)
        accepted = []
        for s, h, dist in zip(items, hashes, cross):
            if dist <= NEAR_DUP_HAMMING:
                dropped["cross_split"] += 1
                continue
            if accepted and _hamming_min(np.array([h], dtype=np.uint64),
                                         np.array(accepted, dtype=np.uint64))[0] <= NEAR_DUP_HAMMING:
                dropped["within_split"] += 1
                continue
            accepted.append(int(h))
            final.append(s)
        if split in ref:
            ref[split] = accepted
    stats["near_duplicates_dropped"] = dropped

    # YOLO directory layout
    for split in ("train", "val", "test"):
        os.makedirs(os.path.join(OUT_DIR, "images", split), exist_ok=True)
        os.makedirs(os.path.join(OUT_DIR, "labels", split), exist_ok=True)
    label_rows = []
    for s in final:
        img_dst = os.path.join(OUT_DIR, "images", s["split"], f"{s['image_id']}.jpg")
        if not os.path.exists(img_dst):
            os.link(os.path.join(IMAGE_CACHE, f"{s['image_id']}.jpg"), img_dst)
        lines = []
        for c, x1, y1, x2, y2 in s["boxes"]:
            cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
            lines.append(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            label_rows.append((s["image_id"], s["split"], c, f"{cx:.6f}", f"{cy:.6f}", f"{w:.6f}", f"{h:.6f}"))
        with open(os.path.join(OUT_DIR, "labels", s["split"], f"{s['image_id']}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")

    with open(os.path.join(OUT_DIR, "data.yaml"), "w") as f:
        f.write(f"# generated by ml/datasets/openimages.py — do not edit\n"
                f"path: {OUT_DIR}\ntrain: images/train\nval: images/val\ntest: images/test\n"
                f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(names)))

    # Per-split / per-class statistics
    split_stats = {}
    for split in ("train", "val", "test"):
        items = [s for s in final if s["split"] == split]
        split_stats[split] = {
            "images": len(items),
            "boxes": {n: sum(1 for s in items for b in s["boxes"] if b[0] == i) for i, n in enumerate(names)},
            "images_with_class": {n: sum(1 for s in items if any(b[0] == i for b in s["boxes"]))
                                  for i, n in enumerate(names)},
        }
    stats["splits"] = split_stats

    label_rows.sort()
    version = hashlib.sha256("\n".join(",".join(map(str, r)) for r in label_rows).encode()).hexdigest()[:16]
    _write_manifest(final, label_rows, stats, version)
    log.info("dataset %s version %s: %s", DATASET_NAME, version,
             {k: v["images"] for k, v in split_stats.items()})
    return version


def _write_manifest(final: list[dict], label_rows: list[tuple], stats: dict, version: str):
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    with open(os.path.join(MANIFEST_DIR, "images.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image_id", "split", "oi_subset", "width", "height", "n_boxes", "classes",
                    "dhash", "license", "author", "author_profile_url", "landing_url", "original_md5"])
        names = class_names()
        for s in sorted(final, key=lambda s: (s["split"], s["image_id"])):
            w.writerow([s["image_id"], s["split"], s["oi_subset"], s["width"], s["height"],
                        len(s["boxes"]), ";".join(sorted({names[b[0]] for b in s["boxes"]})),
                        f"{s['dhash']:016x}", s["license"], s["author"], s["author_profile_url"],
                        s["landing_url"], s["original_md5"]])
    with gzip.open(os.path.join(MANIFEST_DIR, "labels_yolo.csv.gz"), "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image_id", "split", "class_id", "cx", "cy", "w", "h"])
        w.writerows(label_rows)
    summary = {
        "dataset": DATASET_NAME,
        "version": version,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "class_schema": load_schema()["version"],
        "classes": class_names(),
        "source": "Open Images V7 (https://storage.googleapis.com/openimages/web/index.html)",
        "source_files": {k: {kk: vv for kk, vv in v.items() if kk != "s3_folder"} for k, v in SOURCES.items()},
        "image_mirror": S3_IMAGE_URL,
        "licenses": {"annotations": "CC BY 4.0 (Google LLC)",
                     "images": "listed as CC BY 2.0 by Open Images; per-image author/licence in images.csv"},
        "processing": {"max_side": MAX_SIDE, "min_box_side": MIN_BOX_SIDE, "dedup_iou": DEDUP_IOU,
                       "near_dup_hamming": NEAR_DUP_HAMMING, "seed": SEED, "caps": CAPS},
        "stats": stats,
    }
    with open(os.path.join(MANIFEST_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)


def _selection_from_manifest() -> tuple[list[dict], dict]:
    labels = pd.read_csv(os.path.join(MANIFEST_DIR, "labels_yolo.csv.gz"), dtype={"image_id": str})
    images = pd.read_csv(os.path.join(MANIFEST_DIR, "images.csv"), dtype={"image_id": str})
    boxes = {}
    for r in labels.itertuples(index=False):
        boxes.setdefault(r.image_id, []).append(
            (int(r.class_id), r.cx - r.w / 2, r.cy - r.h / 2, r.cx + r.w / 2, r.cy + r.h / 2))
    selection = [{**{k: getattr(r, k) for k in ("image_id", "split", "oi_subset", "license", "author",
                                                 "author_profile_url", "landing_url", "original_md5")},
                  "boxes": boxes[r.image_id]} for r in images.itertuples(index=False)]
    with open(os.path.join(MANIFEST_DIR, "summary.json")) as f:
        stats = json.load(f)["stats"]
    return selection, stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["fetch", "select", "download", "build", "all", "reproduce"])
    p.add_argument("--workers", type=int, default=32)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.stage in ("fetch", "all"):
        fetch()
    if args.stage in ("select", "all"):
        select()
    if args.stage in ("download", "build", "all"):
        with open(os.path.join(OUT_DIR, "selection.json")) as f:
            sel = json.load(f)
        selection, stats = sel["images"], sel["stats"]
        status = download(selection, args.workers)
        if args.stage != "download":
            build(selection, status, stats)
    if args.stage == "reproduce":
        selection, stats = _selection_from_manifest()
        status = download(selection, args.workers)
        build(selection, status, stats)


if __name__ == "__main__":
    sys.exit(main())
