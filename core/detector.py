# core/detector.py
# YOLOv8 detection wrapper.
# Loads a model from models/registry.yaml once, runs inference (optionally with
# ByteTrack tracking) per frame and returns detections with canonical SentinelX
# class names, independent of whether the weights are COCO or SentinelX.

import hashlib
import logging
import os
import time

import cv2
import numpy as np
import yaml

import config

log = logging.getLogger("sentinelx.detector")

# COCO ids kept from the baseline model → canonical names
COCO_CANONICAL = {0: "person", 24: "backpack", 26: "handbag", 28: "suitcase",
                  34: "blunt_tool", 39: "bottle", 43: "knife", 76: "scissors"}


def load_registry(path: str = config.MODEL_REGISTRY) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f).get("models", [])


def _abs(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(config.BASE_DIR, p)


def resolve_model(model_id: str = config.MODEL_ID) -> dict:
    """Pick a registry entry. 'auto' = the entry marked default whose weights
    exist, else the COCO baseline."""
    models = load_registry()
    if model_id != "auto":
        for m in models:
            if m["id"] == model_id:
                if not os.path.exists(_abs(m["weights"])):
                    raise FileNotFoundError(f"weights for model {model_id!r} not found: {m['weights']}")
                return m
        raise KeyError(f"model {model_id!r} not in {config.MODEL_REGISTRY}")
    for m in models:
        if m.get("default") and os.path.exists(_abs(m["weights"])):
            return m
    for m in models:
        if m["schema"] == "coco" and os.path.exists(_abs(m["weights"])):
            return m
    return {"id": "yolov8n-coco", "weights": config.YOLO_MODEL, "schema": "coco"}


def pick_device(requested: str = config.YOLO_DEVICE) -> str:
    if requested:
        return requested
    try:
        import torch
        if torch.cuda.is_available():
            return "0"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:   # torch missing / broken → CPU
        pass
    return "cpu"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Detector:
    def __init__(self, model_id: str = config.MODEL_ID, tracking: bool = True,
                 device: str | None = None):
        from ultralytics import YOLO   # heavy import kept local

        self.spec = resolve_model(model_id)
        self.version = self.spec["id"]
        self.device = device or pick_device()
        weights = _abs(self.spec["weights"])
        self.model = YOLO(weights)
        self.class_names = self.model.names          # index → model class name
        if self.spec["schema"] == "coco":
            self.class_map = {i: n for i, n in COCO_CANONICAL.items() if i in config.YOLO_CLASSES}
        else:
            self.class_map = dict(self.class_names)
        self.model_classes = sorted(self.class_map)
        self.tracking = bool(tracking and config.TRACKER_CONFIG)
        self.errors = 0
        self.last_latency_ms = 0.0
        log.info("loaded model %s (%s) on %s, tracking=%s", self.version, weights, self.device, self.tracking)

    def info(self) -> dict:
        return {"id": self.version, "name": self.spec.get("name", self.version),
                "schema": self.spec["schema"], "device": self.device,
                "tracking": "bytetrack" if self.tracking else "iou",
                "classes": sorted(set(self.class_map.values()))}

    def detect(self, frame: np.ndarray, track: bool | None = None) -> list[dict]:
        """
        Run YOLOv8 on a single frame.

        Returns a list of detections, each as:
        {
            "class_id":   int,      model class index
            "class_name": str,      canonical name, e.g. "person", "knife"
            "confidence": float,
            "bbox":       (x1, y1, x2, y2)  — pixel coordinates
            "track_id":   int | None        — ByteTrack id when tracking
        }
        Inference errors are logged and yield an empty list (never fake output).
        """
        track = self.tracking if track is None else track
        kwargs = dict(classes=self.model_classes, conf=config.YOLO_CONFIDENCE,
                      imgsz=config.YOLO_IMGSZ, device=self.device, verbose=False)
        t0 = time.perf_counter()
        try:
            if track:
                results = self.model.track(frame, persist=True, tracker=config.TRACKER_CONFIG, **kwargs)[0]
            else:
                results = self.model.predict(frame, **kwargs)[0]
        except Exception as e:          # noqa: BLE001 — keep the stream alive
            self.errors += 1
            if track and self.errors <= 3:
                log.warning("tracking failed (%s); falling back to detection + IoU tracker", e)
                self.tracking = False
                return self.detect(frame, track=False)
            log.exception("inference failed")
            return []
        self.last_latency_ms = (time.perf_counter() - t0) * 1000

        detections = []
        boxes = results.boxes
        if boxes is None or len(boxes) == 0:
            return detections
        ids = boxes.id.int().tolist() if (track and boxes.id is not None) else [None] * len(boxes)
        for cls, conf, xyxy, tid in zip(boxes.cls.int().tolist(), boxes.conf.tolist(),
                                        boxes.xyxy.tolist(), ids):
            name = self.class_map.get(cls)
            if name is None:
                continue
            detections.append({
                "class_id":   cls,
                "class_name": name,
                "confidence": round(float(conf), 4),
                "bbox":       tuple(int(v) for v in xyxy),
                "track_id":   tid,
            })
        return detections

    def reset_tracking(self):
        """Forget ByteTrack state (on video switch / reconnect)."""
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None and getattr(predictor, "trackers", None):
            for t in predictor.trackers:
                t.reset()

    def annotate_frame(self, frame: np.ndarray, detections: list[dict]) -> np.ndarray:
        """
        Draw bounding boxes and labels onto the frame.
        Returns the annotated frame (does not modify original).
        """
        annotated = frame.copy()

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            label = f'{det["class_name"]} {det["confidence"]:.2f}'

            # Color: blue for persons, orange for bags
            color = (255, 100, 0) if det["class_name"] == "person" else (0, 165, 255)

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                annotated, label,
                (x1, y1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2
            )

        return annotated
