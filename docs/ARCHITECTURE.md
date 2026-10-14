# Architecture

```
                     ┌───────────────────────────── per camera thread (core/camera.py) ─────────────────────────────┐
 RTSP / HTTP /       │                                                                                               │
 webcam / file ──▶   │  capture ─▶ Detector (YOLOv8, ByteTrack) ─▶ PersonTracker v2 ─▶ MotionMeter (OpenCV cues)    │
 (reconnect,         │               core/detector.py              core/tracker.py       core/temporal.py           │
  real-time replay)  │                         │                       │ dwell, posture,       │ motion, brightness,  │
                     │                         │                       │ speed per track       │ sharpness            │
                     │                         ▼                       ▼                       ▼                      │
                     │                ThreatEngine v2 (rules + temporal persistence + learned temporal head)         │
                     │                core/threat_engine.py           core/temporal.py (TemporalHead)               │
                     │                         │ score 0-100, level, signals[]                                        │
                     │                         ▼                                                                      │
                     │                IncidentManager ──▶ AlertManager (severity gate, cooldown, de-dup, webhook)     │
                     │                core/incidents.py    core/alerts.py                                            │
                     │                         │ evidence (snapshot + pre-roll clip, SHA-256)                         │
                     └─────────────────────────┼─────────────────────────────────────────────────────────────────────┘
                                               ▼
                 SQLite (WAL, migrations, FTS5)  ◀──  core/repository.py  ◀──  Flask API (web/api.py, RBAC, CSRF, audit)
                 instance/sentinelx.db                                              │
                 evidence files on disk ─────────────────────────────────────────────┤
                                                                                     ▼
                                                     Dashboard (templates/ + static/js, no build step)
```

## Components

| Module | Responsibility |
|---|---|
| `config.py` | All settings, overridable as `SENTINELX_*` env vars or via `.env` |
| `core/detector.py` | Loads a model from `models/registry.yaml` (COCO baseline or a SentinelX fine-tune), maps classes to canonical names, ByteTrack tracking with an IoU fallback, never fabricates output on errors |
| `core/tracker.py` | Persistent IDs (ByteTrack or IoU+centroid), zone dwell time, crouching/tampering posture, speed in body-heights/s, rapid motion. All timing uses media time for files and wall time for live streams |
| `core/zones.py` | Per-camera polygons (normalised) for the ATM zone and restricted areas |
| `core/temporal.py` | OpenCV motion/brightness/sharpness cues, windowed behaviour features, learned logistic temporal head |
| `core/threat_engine.py` | Rules → signals with weights and evidence; object rules require temporal persistence; time-based rise/hold/decay smoothing; camera-tamper and learned-anomaly signals |
| `core/incidents.py` | Incident lifecycle (open at threshold, escalate, close after a quiet period), detection sampling, evidence |
| `core/alerts.py` | Severity-gated alerts with per-camera cooldown, de-duplication, escalation bypass, optional webhook |
| `core/db.py`, `db/migrations/` | Versioned, checksummed SQL migrations in transactions; a backup is taken before migrating; online backup/restore |
| `core/repository.py` | All SQL (parameterised): incidents, detections, evidence, alerts, audit, users, settings, stats |
| `core/evidence.py` | Private evidence storage with hashing, path confinement and retention purge |
| `core/runtime_settings.py` | Admin-tunable thresholds with validation and audit |
| `web/` | App factory, auth/RBAC/CSRF, REST API, pages, v0-compatible endpoints |
| `ml/` | Dataset builders, training, detector and pipeline evaluation, temporal-head training, frozen v0 baseline |
| `api/index.py` | Serverless (Vercel) demo: same UI, precomputed detections only, clearly labelled as demo |

## Data provenance in the UI
Every camera, incident and alert carries `source_kind`:
- **live**: RTSP/HTTP/webcam stream
- **recorded**: a video file replayed in real time through the live pipeline
- **demo**: precomputed detections served by the serverless build

The dashboard shows this on every feed, incident row and alert. Research ground-truth labels (ATMA-V) appear
only for labelled research videos, and they are explicitly marked "research label".

## Failure handling
- **Camera unavailable:** status `offline`, exponential reconnect backoff (1 → 30 s), surfaced in the UI and `/api/health`.
- **Bad file / invalid upload:** checked against an extension allow-list, then size limit, and must decode; otherwise rejected with a 400.
- **Inference error:** logged and counted per camera. Tracking failure falls back to detection plus the IoU tracker. The frame yields no detections rather than invented ones.
- **Database error:** the incident manager logs and continues, so the video pipeline never stops. Migrations roll back on failure.
- **API errors:** JSON responses with status codes; the dashboard shows toasts and keeps the previous render.
