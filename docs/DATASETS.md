# Datasets

SentinelX uses two kinds of labels, and they are never mixed:

| Label type | Used for | Datasets |
|---|---|---|
| **Bounding boxes** (object detection) | training / validating / testing the detector | Open Images V7 — SentinelX subset |
| **Video-level / temporal anomaly labels** | calibrating and evaluating the end-to-end threat pipeline | ATMA-V, Surveillance Camera Fight Dataset |

Every fact below was checked against the official source on **2026-10-14**.
The machine-readable version is [`data/registry/datasets.yaml`](../data/registry/datasets.yaml).

## Acquired

### 1. Open Images V7 — SentinelX subset (`openimages_v7_sentinelx_v1`)

- **Source:** <https://storage.googleapis.com/openimages/web/index.html> (official annotation CSVs on
  `storage.googleapis.com/openimages`, images from the official CVDF mirror `open-images-dataset.s3.amazonaws.com`).
- **Licence:** annotations CC BY 4.0 (Google LLC); images are *listed* as CC BY 2.0. Google states it makes no
  warranty about each image's licence, so every image's author, licence URL and landing page are recorded in
  [`images.csv`](../data/manifests/openimages_v7_sentinelx_v1/images.csv).
- **What is committed:** image IDs, per-image attribution, derived YOLO labels
  (`labels_yolo.csv.gz`, CC BY 4.0 derivative) and the build summary. **Images are not committed**; they are
  re-downloaded by script.
- **Classes** ([`classes.yaml`](../data/registry/classes.yaml)): person (Person/Man/Woman/Boy/Girl), knife
  (Knife/Kitchen knife/Dagger), firearm (Handgun/Rifle/Shotgun), backpack, handbag, suitcase, bottle, helmet
  (Helmet/Bicycle helmet), blunt_tool (Hammer/Baseball bat).
- **Cleaning** (counts in [`summary.json`](../data/manifests/openimages_v7_sentinelx_v1/summary.json)):
  - images with any *IsDepiction* (drawings/toys) or *IsGroupOf* (crowd boxes) annotation for a SentinelX class are removed;
  - images whose EXIF rotation is unknown or non-zero are removed (box coordinates would not match pixels);
  - images where a class was **human-verified present** (image-level label, confidence 1) but has no box are
    removed — they contain unboxed objects that would be learned as background;
  - boxes are clipped to the image, degenerate boxes dropped, and same-class duplicates (IoU > 0.85, e.g. a
    Person box and a Man box on one human) merged;
  - every downloaded file is decoded and verified; near-duplicate images (64-bit dHash, Hamming ≤ 4) are removed
    within each split and across splits.
- **Leakage-safe split:** val/test come from the official OI *validation* + *test* subsets (denser human
  verification), partitioned **by photographer** (Flickr author); train comes from the official OI *train*
  subset with every held-out photographer and every exact-duplicate file (MD5) excluded. A few very prolific
  weapon photographers (> 20 rare-class images and > 5× more in train than held-out) are kept on the train side
  so they cannot dominate the test set. Result: **zero photographer overlap** between train, val and test.
- **Size:** 6,178 train / 906 val / 1,437 test images (version `6495ad20fbf022c7`).
- **Reproduce exactly:** `python -m ml.datasets.openimages reproduce` (downloads the listed images, rebuilds labels).
  **Rebuild from scratch:** `python -m ml.datasets.openimages all` (streams ~5.6 GB of official CSVs, keeps only
  SentinelX rows, resumes with HTTP Range if interrupted).
- **Known limitation:** Open Images boxes are exhaustive only for classes annotators verified, so some
  unannotated people/objects remain in training images. Held-out counts for `blunt_tool` (16 test images),
  `suitcase` (38) and `backpack` (44) are small — treat those per-class numbers as low-confidence.

### 2. ATMA-V — ATM Anomaly Video Dataset (`atmav`)

- **Source:** <https://www.kaggle.com/datasets/mehantkammakomati/atm-anomaly-video-dataset-atmav>
- **Licence:** CC BY-SA 4.0 as declared on Kaggle. The uploader states the clips were collected from YouTube,
  stock-footage and news sites; see [`data/videos/ATTRIBUTION.md`](../data/videos/ATTRIBUTION.md) for the
  provenance caveat.
- **Labels:** frame ranges of anomalous activity (1-indexed, inclusive) for 65 videos — *temporal labels, no boxes*.
- **Validation:** all 65 local files match the Kaggle listing byte-for-byte; 0 label errors. 14 containers
  over-report their frame count by 1–131 frames; every labelled segment ends within the decodable frames.
- **Split:** group-aware by source recording (`30a`/`30b`, `36a`–`36c`, `67a`/`67b`, `82a`/`82b` stay together),
  stratified by anomalous/normal: 32 train / 12 val / 21 test videos. Train fits the temporal head, val selects
  its hyper-parameters, test is reported only.
- **Acquire:** already in `data/videos/`; or `python -m ml.datasets.atmav --download` (Kaggle API token).

### 3. Surveillance Camera Fight Dataset (`scfd`)

- **Source:** <https://github.com/seymanurakti/fight-detection-surv-dataset> — Aktı, Tataroğlu, Ekenel,
  *Vision-based Fight Detection from Surveillance Cameras*, IPTA 2019.
- **Licence:** MIT (repository LICENSE). Fight clips were cut from YouTube videos, so the clips are fetched on
  demand and **not committed**.
- **Labels:** 300 clips (~2 s), clip-level fight / noFight. Grouped by source video (46 groups, including the
  CamNet and Synopsis ranges) → 121 val / 179 test clips.
- **Acquire:** `python -m ml.datasets.scfd` (archive SHA-256 recorded in the manifest).

## Investigated, not acquired

| Dataset | Access / licence (official source) | Why not acquired | Reproducible alternative |
|---|---|---|---|
| **UCF-Crime** (Sultani et al., CVPR 2018) | Official page: "All data is only for research purposes, unless stated differently." Archive `UCF_Crimes.zip` is **102,957,372,377 bytes** | Larger than the 48 GB of free disk on the build machine; the UCF server sends an incomplete TLS chain, and the scripts never disable certificate verification; labels are video-level (temporal for test only) | `python -m ml.datasets.ucf_crime --accept-research-terms [--videos] [--ca-bundle …]` checks disk space, resumes, and parses the temporal annotations |
| **XD-Violence** | No licence stated on the official page; Baidu/OneDrive links | No usage terms | — |
| **RWF-2000** | No redistribution / commercial use without SMIIP Lab approval; videos currently not available | Not publicly downloadable | Request access from the authors |
| **CUHK Avenue** | No licence stated (citation requested) | No terms; campus walkway, low ATM relevance | — |
| **ShanghaiTech Campus** | Licence not stated on the official page (third parties claim BSD-2) | Could not verify the licence | — |
| **UCSD Ped1/Ped2** | Official site refused connections during research | Unreachable; pedestrian walkways only | — |
| **UBnormal** | CC BY-NC-ND 4.0 | Synthetic scenes; NoDerivatives forbids sharing derived labels | — |
| **VIRAT** (ground camera) | "VIRAT Video Dataset Protection Agreement" | Requires a signed agreement | Apply via Kitware's portal |
| **OD-WeaponDetection** (DaSCI) | README says CC BY-SA 4.0, repository sidebar says CC BY 4.0 | Inconsistent licence statement; Google Drive archive (no verifiable scripted download) | Candidate for v2 once the authors confirm the licence |

No ATM-specific dataset with **bounding boxes** and a clear licence was found; this is why the detector is
trained on general-purpose Open Images photos and the ATM-specific behaviour is calibrated and evaluated on
ATMA-V's temporal labels.
