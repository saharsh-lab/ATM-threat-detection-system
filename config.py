# config.py
import os

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR     = os.path.join(BASE_DIR, "data", "videos")
DEFAULT_VIDEO = os.path.join(VIDEO_DIR, "28.mp4")

# ── YOLO ──────────────────────────────────────────────────────────────────
YOLO_MODEL      = "yolov8n.pt"
YOLO_CONFIDENCE = 0.30          # lower threshold catches more objects

YOLO_CLASSES = [
    0,   # person
    24,  # backpack
    26,  # handbag
    28,  # suitcase
    39,  # bottle  ← fuel bottle, chemical bottle
    43,  # knife   ← bladed weapon
    76,  # scissors (sometimes detected instead of knife)
]

# ── Tracker ───────────────────────────────────────────────────────────────
LOITERING_THRESHOLD_SECONDS = 20
TRACK_EXPIRY_SECONDS        = 6.0   # tracks survive longer during occlusion
IOU_MATCH_THRESHOLD         = 0.20  # more lenient matching
CENTER_MATCH_MAX_DIST       = 150   # px — fallback match distance

# ── Threat Scoring ────────────────────────────────────────────────────────
SCORE_MULTIPLE_PERSONS   = 20
SCORE_LOITERING          = 25
SCORE_UNATTENDED_BAG     = 15
SCORE_RESTRICTED_ZONE    = 15
SCORE_VANDALISM_BEHAVIOR = 30
SCORE_WEAPON_OBJECT      = 40   # knife detected
SCORE_THREAT_OBJECT      = 25   # bottle / suspicious object in ATM area

# ── Score Stability (fixes flickering) ────────────────────────────────────
# Score rises quickly toward threat, falls slowly after threat clears.
SCORE_RISE_STEP     = 12    # max points gained per frame update
SCORE_DECAY_STEP    = 3     # max points lost per frame update (slow decay)
SCORE_HOLD_SECONDS  = 4.0   # seconds threat must be GONE before score drops

# ── Risk Levels ───────────────────────────────────────────────────────────
RISK_LEVELS = [
    (90, "CRITICAL"),
    (75, "HIGH"),
    (50, "MEDIUM"),
    (25, "LOW"),
    (0,  "NORMAL"),
]

# ── Restricted Zone ───────────────────────────────────────────────────────
# Nearly full frame — works across all camera angles
# Only a thin margin excluded at edges
RESTRICTED_ZONE = (0.05, 0.05, 0.95, 0.95)

# ── Vandalism Detection ───────────────────────────────────────────────────
VANDALISM_DURATION_SECONDS = 6
VANDALISM_ASPECT_RATIO     = 0.85

# ── Dashboard ─────────────────────────────────────────────────────────────
FLASK_HOST  = "0.0.0.0"
FLASK_PORT  = 5001
STREAM_FPS  = 60