"""
Surveillance Camera Fight Dataset (SCFD) — download, validation and
source-video-grouped splits.

Source:   https://github.com/seymanurakti/fight-detection-surv-dataset
License:  MIT (repository LICENSE). Fight clips were cut from YouTube videos
          (listed in videos.txt); upstream footage rights belong to the
          uploaders, so the clips are downloaded on demand and NOT committed.
Citation: Aktı, Tataroğlu, Ekenel, "Vision-based Fight Detection from
          Surveillance Cameras", IEEE/EURASIP IPTA 2019.

300 clips of ~2 s, clip-level label fight / noFight. Used only to evaluate the
pipeline's fighting/struggle rules (video-level labels, no boxes).

  python -m ml.datasets.scfd
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sys
import tarfile

import requests

from ml.classes import REPO_ROOT
from ml.datasets.atmav import probe_video, sha256_file

log = logging.getLogger("sentinelx.scfd")

URL = "https://codeload.github.com/seymanurakti/fight-detection-surv-dataset/tar.gz/refs/heads/master"
RAW = os.path.join(REPO_ROOT, "datasets", "raw", "scfd")
ARCHIVE = os.path.join(RAW, "archive", "scfd.tar.gz")
EXTRACT = os.path.join(RAW, "extracted")
ROOT_NAME = "fight-detection-surv-dataset-master"
MANIFEST_DIR = os.path.join(REPO_ROOT, "data", "manifests", "scfd")
SEED = 11


def download():
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
    if not os.path.exists(ARCHIVE):
        with requests.get(URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(ARCHIVE + ".part", "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        os.replace(ARCHIVE + ".part", ARCHIVE)
    os.makedirs(EXTRACT, exist_ok=True)
    with tarfile.open(ARCHIVE) as tar:
        members = [m for m in tar.getmembers()
                   if m.isfile() and re.fullmatch(rf"{ROOT_NAME}/(fight|noFight)/[\w-]+\.mp4|{ROOT_NAME}/"
                                                 rf"(videos\.txt|LICENSE|README\.md)", m.name)]
        tar.extractall(EXTRACT, members=members, filter="data")


def parse_groups(videos_txt: str) -> dict:
    """clip id -> source YouTube video id (clips from one source share a group)."""
    groups, current = {}, None
    with open(videos_txt, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            m = re.search(r"(?:v=|youtu\.be/)([\w-]{6,})", line)
            if m:
                current = m.group(1)
                continue
            # range lines: "nofi062 - nofi076 : CamNet Dataset" (first claim wins on overlap)
            m = re.match(r"(nofi|fi)(\d+)\s*-\s*(?:nofi|fi)(\d+)\s*:\s*(.+)", line)
            if m:
                src = "ds-" + re.sub(r"\W+", "-", m.group(4).strip().lower())
                for i in range(int(m.group(2)), int(m.group(3)) + 1):
                    groups.setdefault(f"{m.group(1)}{i:03d}", src)
                continue
            m = re.match(r"(n?o?fi\d+)\s*:", line)
            if m and current:
                groups[m.group(1)] = current
    return groups


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download()
    root = os.path.join(EXTRACT, ROOT_NAME)
    groups = parse_groups(os.path.join(root, "videos.txt"))
    clips, missing_group = [], []
    for label_dir, label in (("fight", 1), ("noFight", 0)):
        for f in sorted(os.listdir(os.path.join(root, label_dir))):
            if not f.endswith(".mp4"):
                continue
            cid = f[:-4]
            path = os.path.join(root, label_dir, f)
            g = groups.get(cid)
            if g is None:
                missing_group.append(cid)
                g = f"ungrouped-{cid}"
            clips.append({"clip": f"{label_dir}/{f}", "id": cid, "label": label, "group": g,
                          "sha256": sha256_file(path), **probe_video(path)})
    # group-level split: val (rule tuning) / test (reporting)
    all_groups = sorted({c["group"] for c in clips})
    random.Random(SEED).shuffle(all_groups)
    n_val = round(len(all_groups) * 0.4)
    split_of = {g: ("val" if i < n_val else "test") for i, g in enumerate(all_groups)}
    for c in clips:
        c["split"] = split_of[c["group"]]
    unreadable = [c["clip"] for c in clips if not c.get("readable") or c.get("frames_decoded", 0) < 5]
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    summary = {
        "dataset": "SCFD", "source": "https://github.com/seymanurakti/fight-detection-surv-dataset",
        "archive_url": URL, "archive_sha256": sha256_file(ARCHIVE), "license_declared": "MIT",
        "label_type": "clip-level (fight / noFight)", "split_policy": "grouped by source YouTube video",
        "split_seed": SEED,
        "counts": {s: {"fight": sum(1 for c in clips if c["split"] == s and c["label"]),
                       "noFight": sum(1 for c in clips if c["split"] == s and not c["label"])}
                   for s in ("val", "test")},
        "groups": len(all_groups), "clips_without_source_group": missing_group, "unreadable": unreadable,
        "clips": clips,
    }
    with open(os.path.join(MANIFEST_DIR, "manifest.json"), "w") as f:
        json.dump(summary, f, indent=2)
    log.info("SCFD: %d clips, %d groups, %s, %d without group, %d unreadable",
             len(clips), len(all_groups), summary["counts"], len(missing_group), len(unreadable))


if __name__ == "__main__":
    sys.exit(main())
