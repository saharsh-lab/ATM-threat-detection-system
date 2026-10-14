# config.py
# SentinelX configuration.
#
# Every value can be overridden with an environment variable of the same name
# prefixed with SENTINELX_ (e.g. SENTINELX_FLASK_PORT=8080). A local `.env`
# file is loaded if present (see .env.example). Secrets are never hard-coded.
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path: str):
    """Minimal .env loader (KEY=VALUE lines); existing env vars win."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env(name, default, cast=str):
    raw = os.environ.get(f"SENTINELX_{name}")
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    try:
        return cast(raw)
    except ValueError as e:
        raise ValueError(f"Invalid value for SENTINELX_{name}={raw!r}: {e}") from None


def _path(name, default):
    p = _env(name, default)
    return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)


# ── Runtime / paths ───────────────────────────────────────────────────────
ENV           = _env("ENV", "development")          # development | production
VIDEO_DIR     = _path("VIDEO_DIR", os.path.join("data", "videos"))
DEFAULT_VIDEO = os.path.join(VIDEO_DIR, _env("DEFAULT_VIDEO", "28.mp4"))
INSTANCE_DIR  = _path("INSTANCE_DIR", "instance")    # git-ignored runtime state
DB_PATH       = _path("DB_PATH", os.path.join("instance", "sentinelx.db"))
EVIDENCE_DIR  = _path("EVIDENCE_DIR", os.path.join("instance", "evidence"))
BACKUP_DIR    = _path("BACKUP_DIR", os.path.join("instance", "backups"))
LOG_DIR       = _path("LOG_DIR", os.path.join("instance", "logs"))
CAMERAS_FILE  = _path("CAMERAS_FILE", os.path.join("config", "cameras.yaml"))
CAMERAS_EXAMPLE_FILE = os.path.join(BASE_DIR, "config", "cameras.example.yaml")
MODEL_REGISTRY = os.path.join(BASE_DIR, "models", "registry.yaml")
LOG_LEVEL     = _env("LOG_LEVEL", "INFO")

# ── YOLO ──────────────────────────────────────────────────────────────────
# MODEL_ID selects an entry in models/registry.yaml. "auto" picks the entry
# marked `default: true` whose weights exist on disk, else the COCO baseline.
MODEL_ID        = _env("MODEL_ID", "auto")
YOLO_MODEL      = _env("YOLO_MODEL", "yolov8n.pt")    # baseline weights (COCO-80)
YOLO_CONFIDENCE = _env("YOLO_CONFIDENCE", 0.30, float)
YOLO_IMGSZ      = _env("YOLO_IMGSZ", 640, int)
YOLO_DEVICE     = _env("YOLO_DEVICE", "")              # "" = auto, "cpu", "mps", "0"
TRACKER_CONFIG  = _env("TRACKER_CONFIG", "bytetrack.yaml")   # "" disables ByteTrack

# COCO class ids used by the baseline model (unchanged from v0)
YOLO_CLASSES = [
    0,   # person
    24,  # backpack
    26,  # handbag
    28,  # suitcase
    39,  # bottle  ← fuel bottle, chemical bottle
    43,  # knife   ← bladed weapon
    76,  # scissors (sometimes detected instead of knife)
]

# Per-class minimum confidence applied after detection (canonical names).
CLASS_CONFIDENCE = {
    "person": 0.35, "knife": 0.40, "firearm": 0.45, "blunt_tool": 0.40,
    "helmet": 0.40, "backpack": 0.30, "handbag": 0.30, "suitcase": 0.30,
    "bottle": 0.35, "scissors": 0.40,
}

# ── Tracker ───────────────────────────────────────────────────────────────
LOITERING_THRESHOLD_SECONDS = _env("LOITERING_THRESHOLD_SECONDS", 20.0, float)
TRACK_EXPIRY_SECONDS        = 6.0   # tracks survive longer during occlusion
IOU_MATCH_THRESHOLD         = 0.20  # more lenient matching
CENTER_MATCH_MAX_DIST       = 150   # px — fallback match distance
TRACK_HISTORY_SECONDS       = 4.0   # positional history kept for motion analysis

# ── Threat Scoring ────────────────────────────────────────────────────────
SCORE_MULTIPLE_PERSONS   = 20
SCORE_LOITERING          = 25
SCORE_UNATTENDED_BAG     = 15
SCORE_RESTRICTED_ZONE    = 15
SCORE_VANDALISM_BEHAVIOR = 30
SCORE_WEAPON_OBJECT      = 40   # knife detected
SCORE_FIREARM            = 50   # firearm detected (SentinelX model only)
SCORE_THREAT_OBJECT      = 25   # bottle / suspicious object in ATM area
SCORE_BLUNT_TOOL         = 25   # hammer / bat in ATM area
SCORE_FACE_CONCEALMENT   = 20   # helmet worn inside the ATM zone
SCORE_RAPID_MOTION       = 20   # sustained erratic / fast movement (struggle)
SCORE_CLOSE_PROXIMITY    = 15   # second person within arm's reach of the user
SCORE_CAMERA_TAMPER      = 40   # lens covered / sprayed / defocused
SCORE_LEARNED_ANOMALY    = 50   # temporal head probability above its validated threshold

# Temporal evidence: an object rule fires only when the object was seen in at
# least OBJECT_PERSISTENCE_MIN_HITS of the detections in the last window.
OBJECT_PERSISTENCE_WINDOW_SECONDS = 1.5
OBJECT_PERSISTENCE_MIN_HITS       = 3
UNATTENDED_BAG_SECONDS            = 10.0
UNATTENDED_BAG_DISTANCE           = 1.5   # in units of nearest-person bbox height
RAPID_MOTION_SPEED                = 1.2   # bbox-heights per second
RAPID_MOTION_SECONDS              = 1.0
CLOSE_PROXIMITY_DISTANCE          = 0.6   # centre distance / mean bbox height
CAMERA_TAMPER_SHARPNESS_DROP      = 1.5   # log-Laplacian-variance drop vs 10 s baseline
CAMERA_TAMPER_DARK                = 0.06  # mean brightness (0-1) treated as a blacked-out lens
CAMERA_TAMPER_SECONDS             = 2.0
TEMPORAL_HEAD_ENABLED             = _env("TEMPORAL_HEAD_ENABLED", True, bool)

# ── Score Stability (fixes flickering) ────────────────────────────────────
# Score rises quickly toward threat, falls slowly after threat clears.
SCORE_RISE_STEP     = 12    # max points gained per frame update
SCORE_DECAY_STEP    = 3     # max points lost per frame update (slow decay)
SCORE_HOLD_SECONDS  = 4.0   # seconds threat must be GONE before score drops
# v2 engine expresses the same behaviour per second so it is FPS-independent
SCORE_RISE_PER_SECOND  = 60.0
SCORE_DECAY_PER_SECOND = 15.0

# ── Risk Levels ───────────────────────────────────────────────────────────
RISK_LEVELS = [
    (90, "CRITICAL"),
    (75, "HIGH"),
    (50, "MEDIUM"),
    (25, "LOW"),
    (0,  "NORMAL"),
]

# ── Zones ─────────────────────────────────────────────────────────────────
# v0 legacy restricted zone (normalised x1, y1, x2, y2). Kept for the legacy
# overlay; per-camera polygons in config/cameras.yaml take precedence.
RESTRICTED_ZONE = (0.05, 0.05, 0.95, 0.95)

# ── Vandalism Detection ───────────────────────────────────────────────────
VANDALISM_DURATION_SECONDS = 6
VANDALISM_ASPECT_RATIO     = 0.85

# ── Incidents & alerts ────────────────────────────────────────────────────
INCIDENT_OPEN_SCORE       = _env("INCIDENT_OPEN_SCORE", 50, int)     # MEDIUM
INCIDENT_CLOSE_SECONDS    = _env("INCIDENT_CLOSE_SECONDS", 10.0, float)
ALERT_MIN_SEVERITY        = _env("ALERT_MIN_SEVERITY", "HIGH")
ALERT_COOLDOWN_SECONDS    = _env("ALERT_COOLDOWN_SECONDS", 60.0, float)
ALERT_WEBHOOK_URL         = _env("ALERT_WEBHOOK_URL", "")
ALERT_WEBHOOK_TIMEOUT     = 5.0
EVIDENCE_CLIP_SECONDS     = _env("EVIDENCE_CLIP_SECONDS", 6.0, float)
EVIDENCE_RETENTION_DAYS   = _env("EVIDENCE_RETENTION_DAYS", 30, int)
DETECTION_SAMPLE_SECONDS  = 1.0   # how often detections are persisted per incident

# ── Dashboard / web ───────────────────────────────────────────────────────
FLASK_HOST  = _env("FLASK_HOST", "127.0.0.1")
FLASK_PORT  = _env("FLASK_PORT", 5001, int)
STREAM_FPS  = _env("STREAM_FPS", 25, int)          # MJPEG output pacing
INFER_FPS   = _env("INFER_FPS", 10.0, float)       # max inference rate per camera
JPEG_QUALITY = 80
SECRET_KEY  = _env("SECRET_KEY", "")               # required in production
SESSION_COOKIE_SECURE = _env("SESSION_COOKIE_SECURE", ENV == "production", bool)
# Development-only escape hatch; refused when ENV=production.
AUTH_DISABLED = _env("AUTH_DISABLED", False, bool)
MAX_UPLOAD_MB = 200
