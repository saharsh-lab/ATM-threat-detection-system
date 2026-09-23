# utils/helpers.py
# Shared utility functions used across modules.

import cv2
import numpy as np
import config


# Color map for risk levels — used in dashboard frame overlay
RISK_COLORS = {
    "NORMAL":   (0, 200, 0),      # green
    "LOW":      (0, 200, 200),    # yellow-green
    "MEDIUM":   (0, 165, 255),    # orange
    "HIGH":     (0, 0, 220),      # red
    "CRITICAL": (0, 0, 180),      # dark red
}


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

    color = RISK_COLORS.get(risk_level, (0, 200, 0))

    # Semi-transparent fill
    overlay = frame.copy()
    cv2.rectangle(overlay, (zx1, zy1), (zx2, zy2), color, -1)
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)

    # Border
    cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), color, 2)
    cv2.putText(frame, "ATM ZONE", (zx1 + 4, zy1 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    return frame


def draw_tracker_info(frame: np.ndarray,
                      tracked_persons: list[dict]) -> np.ndarray:
    """
    Draw person IDs and loitering timers above each tracked person bbox.
    """
    for person in tracked_persons:
        x1, y1, x2, y2 = person["bbox"]
        tid      = person["track_id"]
        duration = person["duration_sec"]
        loitering = person["loitering"]

        label = f"P{tid}  {duration:.0f}s"
        color = (0, 0, 220) if loitering else (255, 100, 0)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        if loitering:
            cv2.putText(frame, "! LOITERING", (x1, y2 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 2)

    return frame


def encode_frame_to_jpeg(frame: np.ndarray) -> bytes:
    """Encode a numpy frame to JPEG bytes for MJPEG streaming."""
    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buffer.tobytes()


def get_risk_color_hex(level: str) -> str:
    """Return hex color string for a risk level — used by frontend JS."""
    mapping = {
        "NORMAL":   "#00c800",
        "LOW":      "#c8c800",
        "MEDIUM":   "#ff8c00",
        "HIGH":     "#dc143c",
        "CRITICAL": "#8b0000",
    }
    return mapping.get(level, "#00c800")