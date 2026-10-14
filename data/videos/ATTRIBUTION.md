# Attribution — ATMA-V videos in this directory

The 65 `.mp4` files in `data/videos/` (and the 5 copies in `static/videos/`)
are the **ATM Anomaly Video Dataset (ATMA-V)** by **Mehant Kammakomati**,
published on Kaggle:
<https://www.kaggle.com/datasets/mehantkammakomati/atm-anomaly-video-dataset-atmav>

**Licence (as declared by the uploader):** Creative Commons
Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) —
<https://creativecommons.org/licenses/by-sa/4.0/>

- These files are **not** covered by the repository's MIT licence.
- They are redistributed unmodified. The frame-range labels in
  `data/labels.py` were transcribed from the dataset's `labels/labels.txt`;
  derived label files and manifests (`data/manifests/atmav/`) are shared
  under the same CC BY-SA 4.0 terms.
- Integrity: every file's byte size matches the Kaggle listing (verified
  2026-10-14); SHA-256 checksums are in `data/manifests/atmav/manifest.json`.

**Provenance caveat.** The dataset description states that the clips were
collected manually from public internet sources (YouTube, Kaotic, Dailymail,
Itemfix, leakedreality, GettyImages, Shutterstock). The Kaggle licence
declaration cannot grant rights the uploader did not hold, so upstream
copyright in individual clips is not guaranteed. Use the videos for research
and evaluation only; if you intend any other use, obtain the footage from the
original rights holders. To remove the videos from your copy, delete this
directory and re-download with `python -m ml.datasets.atmav --download`.
