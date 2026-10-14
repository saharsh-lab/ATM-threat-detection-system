"""
UCF-Crime — opt-in acquisition helper (NOT run by default).

Official page: https://www.crcv.ucf.edu/projects/real-world/
Terms (official page): "All data is only for research purposes, unless stated
differently. Please make sure to reference the authors properly when using the
data."  Sultani, Chen, Shah — CVPR 2018.

Blockers recorded on 2026-10-14:
  * UCF_Crimes.zip is 102,957,372,377 bytes (HTTP Content-Length) — larger than
    the free disk of the development machine (48 GB).
  * www.crcv.ucf.edu serves an incomplete TLS certificate chain, so verified
    HTTPS downloads fail. This script never disables verification; pass
    --ca-bundle with a bundle that includes the missing intermediate, or use
    the official Dropbox mirror linked from the project page manually.

Labels are video-level (train) and temporal frame ranges (test only) — they
are pipeline-evaluation labels, never mixed with bounding boxes.

  python -m ml.datasets.ucf_crime --accept-research-terms                # annotations only
  python -m ml.datasets.ucf_crime --accept-research-terms --videos       # + 103 GB archive
"""

import argparse
import json
import os
import shutil
import sys
import zipfile

import requests

from ml.classes import REPO_ROOT

ANNOT_URL = "https://www.crcv.ucf.edu/projects/real-world/Temporal_Anomaly_Annotation_For_Testing_Videos.zip"
VIDEOS_URL = "https://www.crcv.ucf.edu/data1/chenchen/UCF_Crimes.zip"
VIDEOS_BYTES = 102_957_372_377
RAW = os.path.join(REPO_ROOT, "datasets", "raw", "ucf_crime")
MANIFEST_DIR = os.path.join(REPO_ROOT, "data", "manifests", "ucf_crime")
RELEVANT = {"Robbery", "Burglary", "Fighting", "Vandalism", "Assault", "Stealing", "Shooting", "Normal"}


def fetch(url: str, dest: str, verify):
    """Resumable verified download."""
    pos = os.path.getsize(dest + ".part") if os.path.exists(dest + ".part") else 0
    headers = {"Range": f"bytes={pos}-"} if pos else {}
    with requests.get(url, stream=True, timeout=60, headers=headers, verify=verify) as r:
        r.raise_for_status()
        with open(dest + ".part", "ab" if r.status_code == 206 else "wb") as f:
            for chunk in r.iter_content(1 << 22):
                f.write(chunk)
    os.replace(dest + ".part", dest)


def parse_temporal(txt: str) -> list[dict]:
    """Lines: '<video> <class> <s1> <e1> <s2> <e2>' with -1 for absent segments."""
    out = []
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        v, cls, s1, e1, s2, e2 = parts[:6]
        segs = [(int(a), int(b)) for a, b in ((s1, e1), (s2, e2)) if int(a) >= 0 and int(b) >= 0]
        out.append({"video": v, "class": cls, "segments": segs, "relevant": cls in RELEVANT})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--accept-research-terms", action="store_true", required=True,
                   help="confirm the data is used for research only, with attribution")
    p.add_argument("--videos", action="store_true", help="also download the 103 GB video archive")
    p.add_argument("--ca-bundle", default=None, help="CA bundle containing the server's intermediate cert")
    args = p.parse_args(argv)
    verify = args.ca_bundle or True
    os.makedirs(RAW, exist_ok=True)

    annot_zip = os.path.join(RAW, "Temporal_Anomaly_Annotation_For_Testing_Videos.zip")
    try:
        fetch(ANNOT_URL, annot_zip, verify)
    except requests.exceptions.SSLError as e:
        sys.exit(f"TLS verification failed for {ANNOT_URL} ({e.__class__.__name__}).\n"
                 "The server sends an incomplete certificate chain. Re-run with --ca-bundle, "
                 "or download manually from the official page.")
    with zipfile.ZipFile(annot_zip) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt"))
        rows = parse_temporal(z.read(name).decode("utf-8", "replace"))
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    with open(os.path.join(MANIFEST_DIR, "temporal_test_annotations.json"), "w") as f:
        json.dump({"source": ANNOT_URL, "terms": "research use only, cite Sultani et al. CVPR 2018",
                   "videos": rows}, f, indent=2)
    print(f"parsed {len(rows)} temporal annotations ({sum(r['relevant'] for r in rows)} ATM-relevant classes)")

    if args.videos:
        free = shutil.disk_usage(RAW).free
        if free < VIDEOS_BYTES * 1.1:
            sys.exit(f"need ~{VIDEOS_BYTES * 1.1 / 1e9:.0f} GB free, have {free / 1e9:.0f} GB — aborting")
        fetch(VIDEOS_URL, os.path.join(RAW, "UCF_Crimes.zip"), verify)
        print("video archive downloaded; extract only the relevant class folders you need")


if __name__ == "__main__":
    main()
