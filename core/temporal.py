# core/temporal.py
# Temporal behaviour analysis shared by the live pipeline and offline
# evaluation (one implementation, so features cannot drift).
#
#  MotionMeter       OpenCV frame cues: motion energy inside the ATM zone,
#                    brightness and sharpness (lens covering / spraying /
#                    defocus = camera tampering).
#  TemporalFeatures  per-frame behaviour features from tracks, objects and
#                    motion, aggregated over a sliding window (mean + max).
#  TemporalHead      logistic-regression anomaly head trained offline by
#                    ml/train_temporal.py on the ATMA-V *train* split (model
#                    selection on *val*). Loaded from models/temporal/*.json.

import json
import math
import os
from collections import deque

import cv2
import numpy as np

import config

BASE_FEATURES = [
    "persons_zone", "persons_total", "max_speed", "rapid_motion", "hunching", "vandalism", "close_pairs",
    "new_tracks", "mean_person_conf", "max_dwell", "bladed", "firearm", "blunt", "helmet", "bags", "bottle",
    "motion", "motion_zone", "brightness", "sharpness", "brightness_drop", "sharpness_drop", "no_person_motion",
]
WINDOW_SECONDS = 2.0


class MotionMeter:
    """Cheap per-frame cues on a 160-px grayscale thumbnail."""

    def __init__(self, zones=None, width: int = 160, baseline_seconds: float = 10.0):
        self.zones = zones
        self.width = width
        self._prev = None
        self._mask = None
        self._base = deque()            # (t, brightness, sharpness) for running baseline
        self.baseline_seconds = baseline_seconds

    def _zone_mask(self, h, w):
        if self._mask is None or self._mask.shape != (h, w):
            self._mask = np.zeros((h, w), np.uint8)
            if self.zones is not None:
                pts = np.array([[int(x * w), int(y * h)] for x, y in self.zones.atm_zone], np.int32)
                cv2.fillPoly(self._mask, [pts], 1)
            else:
                self._mask[:] = 1
        return self._mask

    def update(self, frame: np.ndarray, now: float) -> dict:
        h0, w0 = frame.shape[:2]
        h = max(1, int(h0 * self.width / w0))
        g = cv2.cvtColor(cv2.resize(frame, (self.width, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        mask = self._zone_mask(h, self.width)
        brightness = float(g.mean()) / 255.0
        sharpness = math.log1p(float(cv2.Laplacian(g, cv2.CV_32F).var()))
        if self._prev is None or self._prev.shape != g.shape:
            motion = motion_zone = 0.0
        else:
            diff = cv2.absdiff(g, self._prev).astype(np.float32) / 255.0
            motion = float(diff.mean())
            motion_zone = float((diff * mask).sum() / max(mask.sum(), 1))
        self._prev = g
        base = [b for b in self._base if now - b[0] <= self.baseline_seconds]
        b_bright = np.mean([b[1] for b in base]) if base else brightness
        b_sharp = np.mean([b[2] for b in base]) if base else sharpness
        self._base = deque(base + [(now, brightness, sharpness)])
        return {"motion": motion, "motion_zone": motion_zone, "brightness": brightness, "sharpness": sharpness,
                "brightness_drop": max(0.0, b_bright - brightness), "sharpness_drop": max(0.0, b_sharp - sharpness)}

    def reset(self):
        self._prev, self._mask, self._base = None, None, deque()


def frame_features(tracked: list[dict], detections: list[dict], cues: dict, seen_ids: set) -> dict:
    conf_ok = lambda d: d["confidence"] >= config.CLASS_CONFIDENCE.get(d["class_name"], 0.0)  # noqa: E731
    persons = [p for p in tracked if conf_ok(p)]
    in_zone = [p for p in persons if p.get("in_zone", True)]
    objs = [d for d in detections if d["class_name"] != "person" and conf_ok(d)]
    ids = {p["track_id"] for p in persons}
    pairs = 0
    for i, a in enumerate(in_zone):
        for b in in_zone[i + 1:]:
            ca = ((a["bbox"][0] + a["bbox"][2]) / 2, (a["bbox"][1] + a["bbox"][3]) / 2)
            cb = ((b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2)
            hgt = max(((a["bbox"][3] - a["bbox"][1]) + (b["bbox"][3] - b["bbox"][1])) / 2, 1)
            pairs += math.dist(ca, cb) / hgt < config.CLOSE_PROXIMITY_DISTANCE
    count = lambda names: sum(d["class_name"] in names for d in objs)  # noqa: E731
    return {
        "persons_zone": len(in_zone), "persons_total": len(persons),
        "max_speed": min(max((p.get("speed", 0.0) for p in persons), default=0.0), 10.0),
        "rapid_motion": float(any(p.get("rapid_motion") for p in persons)),
        "hunching": float(any(p.get("hunching") and p.get("in_zone", True) for p in persons)),
        "vandalism": float(any(p.get("vandalism") for p in persons)),
        "close_pairs": float(pairs),
        "new_tracks": float(len(ids - seen_ids)),
        "mean_person_conf": float(np.mean([p["confidence"] for p in persons])) if persons else 0.0,
        "max_dwell": min(max((p.get("dwell_sec", 0.0) for p in persons), default=0.0), 120.0) / 60.0,
        "bladed": float(count({"knife", "scissors"})), "firearm": float(count({"firearm"})),
        "blunt": float(count({"blunt_tool"})), "helmet": float(count({"helmet"})),
        "bags": float(count({"backpack", "handbag", "suitcase"})), "bottle": float(count({"bottle"})),
        **{k: cues.get(k, 0.0) for k in ("motion", "motion_zone", "brightness", "sharpness",
                                          "brightness_drop", "sharpness_drop")},
        "no_person_motion": cues.get("motion_zone", 0.0) if not persons else 0.0,
    }


class TemporalFeatures:
    """Sliding-window aggregation (mean and max) of per-frame features."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS):
        self.window = window_seconds
        self._buf = deque()
        self._seen = deque()           # (t, ids) for new-track counting

    def seen_ids(self, now) -> set:
        while self._seen and now - self._seen[0][0] > self.window:
            self._seen.popleft()
        return set().union(*[s[1] for s in self._seen]) if self._seen else set()

    def update(self, now: float, tracked, detections, cues) -> np.ndarray:
        f = frame_features(tracked, detections, cues, self.seen_ids(now))
        self._seen.append((now, {p["track_id"] for p in tracked}))
        self._buf.append((now, [f[k] for k in BASE_FEATURES]))
        while self._buf and now - self._buf[0][0] > self.window:
            self._buf.popleft()
        arr = np.array([b[1] for b in self._buf], dtype=np.float64)
        return np.concatenate([arr.mean(axis=0), arr.max(axis=0)])

    @staticmethod
    def names() -> list[str]:
        return [f"mean_{k}" for k in BASE_FEATURES] + [f"max_{k}" for k in BASE_FEATURES]

    def reset(self):
        self._buf.clear()
        self._seen.clear()


class TemporalHead:
    """Standardised logistic regression: p = sigmoid(w·(x-μ)/σ + b)."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.version = spec["version"]
        self.threshold = float(spec["threshold"])
        self.mean = np.array(spec["mean"])
        self.std = np.array(spec["std"])
        self.coef = np.array(spec["coef"])
        self.intercept = float(spec["intercept"])
        if spec["features"] != TemporalFeatures.names():
            raise ValueError("temporal head was trained on a different feature set")

    def predict(self, x: np.ndarray) -> float:
        z = float(((x - self.mean) / self.std) @ self.coef + self.intercept)
        return 1.0 / (1.0 + math.exp(-max(min(z, 40), -40)))

    @classmethod
    def for_model(cls, model_id: str, directory: str | None = None):
        path = os.path.join(directory or os.path.join(config.BASE_DIR, "models", "temporal"), f"{model_id}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return cls(json.load(f))
