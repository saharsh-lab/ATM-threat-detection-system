# core/detector.py
# YOLOv8 detection wrapper.
# Loads the model once, runs inference per frame,
# returns clean detection results.

import cv2
import numpy as np
from ultralytics import YOLO
import config


class Detector:
    def __init__(self):
        # Load YOLOv8n — downloads automatically on first run
        self.model = YOLO(config.YOLO_MODEL)
        self.class_names = self.model.names  # index → class name string

    def detect(self, frame: np.ndarray) -> list[dict]:
        """
        Run YOLOv8 on a single frame.

        Returns a list of detections, each as:
        {
            "class_id":   int,
            "class_name": str,    e.g. "person", "backpack"
            "confidence": float,
            "bbox":       (x1, y1, x2, y2)  — pixel coordinates
        }
        """
        results = self.model(
            frame,
            classes=config.YOLO_CLASSES,
            conf=config.YOLO_CONFIDENCE,
            verbose=False,   # suppress per-frame console output
        )[0]

        detections = []
        for box in results.boxes:
            class_id   = int(box.cls[0])
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            detections.append({
                "class_id":   class_id,
                "class_name": self.class_names[class_id],
                "confidence": confidence,
                "bbox":       (x1, y1, x2, y2),
            })

        return detections

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