# utils/helpers.py
# Shared utility functions used across modules.

import cv2
import numpy as np
import config

# SentinelX palette (BGR). Obsidian & Signal Red.
SIGNAL_RED = (79, 79, 217)      # #D94F4F
SIGNAL_TEAL = (160, 183, 117)   # #75B7A0
AMBER = (79, 169, 217)          # #D9A94F
FROST = (242, 237, 232)         # #E8EDF2
OBSIDIAN = (20, 16, 13)         # #0D1014

# Color map for risk levels — used in dashboard frame overlay
RISK_COLORS = {
    "NORMAL":   SIGNAL_TEAL,
    "LOW":      (130, 200, 200),
    "MEDIUM":   AMBER,
    "HIGH":     SIGNAL_RED,
    "CRITICAL": (60, 50, 200),
}

THREAT_CLASSES = {"knife", "scissors", "firearm", "blunt_tool"}
BAG_CLASSES = {"backpack", "handbag", "suitcase"}


def draw_restricted_zone(frame: np.ndarray, risk_level: str) -> np.ndarray:
    """
    Draw the restricted ATM zone rectangle on the frame.
    Color changes based on current risk level.
    """
    h, w = frame.shape[:2]
    zx1 = int(config.RESTRICTED_ZONE[0] * w)
    zy1 = int(config.RESTRICTED_ZONE[1] * h)
    zx2 = int(config.RESTRICTED_ZONE[2] * w)
    zy2 = int(config.RESTRICTED_ZONE[3] * h)

    color = RISK_COLORS.get(risk_level, SIGNAL_TEAL)

    # Semi-transparent fill
    overlay = frame.copy()
    cv2.rectangle(overlay, (zx1, zy1), (zx2, zy2), color, -1)
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)

    # Border
    cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), color, 2)
    cv2.putText(frame, "ATM ZONE", (zx1 + 4, zy1 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    return frame


def draw_zones(frame: np.ndarray, zones, risk_level: str) -> np.ndarray:
    """Draw per-camera polygon zones (normalised coords) — subtle outline."""
    h, w = frame.shape[:2]
    color = RISK_COLORS.get(risk_level, SIGNAL_TEAL)
    pts = np.array([[int(x * w), int(y * h)] for x, y in zones.atm_zone], np.int32)
    overlay = frame.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, 0.08, frame, 0.92, 0, frame)
    cv2.polylines(frame, [pts], True, color, 1, cv2.LINE_AA)
    cv2.putText(frame, "ATM ZONE", (pts[0][0] + 6, pts[0][1] + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    for poly in zones.restricted:
        rp = np.array([[int(x * w), int(y * h)] for x, y in poly], np.int32)
        cv2.polylines(frame, [rp], True, SIGNAL_RED, 2, cv2.LINE_AA)
        cv2.putText(frame, "RESTRICTED", (rp[0][0] + 6, rp[0][1] + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, SIGNAL_RED, 1, cv2.LINE_AA)
    return frame


def _label(frame, text, x, y, color):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    y = max(y, th + 6)
    cv2.rectangle(frame, (x, y - th - 6), (x + tw + 8, y), color, -1)
    cv2.putText(frame, text, (x + 4, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, OBSIDIAN, 1, cv2.LINE_AA)


def draw_tracker_info(frame: np.ndarray,
                      tracked_persons: list[dict]) -> np.ndarray:
    """
    Draw person IDs, confidence and dwell timers above each tracked person bbox.
    """
    for person in tracked_persons:
        x1, y1, x2, y2 = person["bbox"]
        tid = person["track_id"]
        dwell = person.get("dwell_sec", person["duration_sec"])
        flagged = person.get("loitering") or person.get("vandalism") or person.get("rapid_motion")
        color = SIGNAL_RED if flagged else SIGNAL_TEAL
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        conf = person.get("confidence")
        conf_txt = f" {conf:.2f}" if conf is not None else ""
        _label(frame, f"ID {tid}{conf_txt}  {dwell:.0f}s", x1, y1 - 2, color)
        tags = [t for t, k in (("LOITERING", "loitering"), ("TAMPERING", "vandalism"),
                               ("RAPID MOTION", "rapid_motion")) if person.get(k)]
        if tags:
            _label(frame, " | ".join(tags), x1, y2 + 18, SIGNAL_RED)
    return frame


def draw_objects(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    for d in detections:
        if d["class_name"] == "person":
            continue
        x1, y1, x2, y2 = d["bbox"]
        color = SIGNAL_RED if d["class_name"] in THREAT_CLASSES else AMBER
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        _label(frame, f'{d["class_name"]} {d["confidence"]:.2f}', x1, y1 - 2, color)
    return frame


def draw_hud(frame: np.ndarray, threat: dict, source_label: str) -> np.ndarray:
    color = RISK_COLORS.get(threat["level"], SIGNAL_TEAL)
    _label(frame, f'{threat["level"]}  {threat["score"]}', 10, 28, color)
    h = frame.shape[0]
    _label(frame, source_label, 10, h - 10, FROST)
    return frame


def encode_frame_to_jpeg(frame: np.ndarray, quality: int = config.JPEG_QUALITY) -> bytes:
    """Encode a numpy frame to JPEG bytes for MJPEG streaming."""
    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buffer.tobytes()


def get_risk_color_hex(level: str) -> str:
    """Return hex color string for a risk level — used by frontend JS."""
    mapping = {
        "NORMAL":   "#75B7A0",
        "LOW":      "#C8C882",
        "MEDIUM":   "#D9A94F",
        "HIGH":     "#D94F4F",
        "CRITICAL": "#C8323C",
    }
    return mapping.get(level, "#75B7A0")
