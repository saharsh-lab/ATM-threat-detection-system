-- 0001_initial_schema.sql
-- SentinelX incident-management schema.
--
-- Before this migration the application kept events only in memory
-- (core/event_logger.py) and had no database, so there is no prior data to
-- carry over. Large media (video clips, snapshots) are NEVER stored in the
-- database: evidence_files holds paths relative to SENTINELX_EVIDENCE_DIR.

CREATE TABLE cameras (
    id            TEXT PRIMARY KEY,                 -- e.g. "cam-01"
    name          TEXT NOT NULL,
    location      TEXT,
    source_type   TEXT NOT NULL CHECK (source_type IN ('file', 'rtsp', 'http', 'webcam')),
    source_ref    TEXT NOT NULL,                    -- file name / env-var name; never raw credentials
    zone_config   TEXT NOT NULL DEFAULT '{}',       -- JSON (core/zones.py)
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE datasets (
    id            TEXT PRIMARY KEY,                 -- e.g. "openimages_v7_sentinelx_v1"
    name          TEXT NOT NULL,
    version       TEXT,
    label_type    TEXT NOT NULL CHECK (label_type IN ('bbox', 'video_temporal', 'video_level')),
    source_url    TEXT,
    license       TEXT,
    access        TEXT,                             -- public / registration / agreement / unavailable
    manifest_path TEXT,
    stats         TEXT NOT NULL DEFAULT '{}',       -- JSON
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE model_versions (
    id             TEXT PRIMARY KEY,                -- e.g. "sentinelx-det-v1"
    name           TEXT NOT NULL,
    weights_path   TEXT NOT NULL,
    weights_sha256 TEXT,
    class_schema   TEXT NOT NULL,
    dataset_id     TEXT REFERENCES datasets(id),
    engine_version TEXT,
    metrics        TEXT NOT NULL DEFAULT '{}',      -- JSON copied from the evaluation report
    registered_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('viewer', 'analyst', 'admin')),
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_login_at TEXT
);

CREATE TABLE incidents (
    id                TEXT PRIMARY KEY,             -- "INC-20261014-3F9A2C"
    camera_id         TEXT NOT NULL REFERENCES cameras(id),
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('live', 'recorded', 'demo')),
    video_ref         TEXT,                         -- file name for recorded sources
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    updated_at        TEXT NOT NULL,
    frame_start       INTEGER,
    frame_end         INTEGER,
    media_time_start  REAL,
    media_time_end    REAL,
    threat_score      INTEGER NOT NULL,             -- latest smoothed score
    peak_score        INTEGER NOT NULL,
    severity          TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'acknowledged', 'investigating', 'resolved', 'false_positive')),
    primary_reason    TEXT,
    reasons           TEXT NOT NULL DEFAULT '[]',   -- JSON list of rule messages seen
    rules             TEXT NOT NULL DEFAULT '[]',   -- JSON list of rule ids seen
    detection_classes TEXT NOT NULL DEFAULT '[]',   -- JSON list of class names seen
    max_confidence    REAL,
    model_version     TEXT REFERENCES model_versions(id),
    engine_version    TEXT,
    dataset_id        TEXT REFERENCES datasets(id), -- training dataset of model_version
    assigned_to       TEXT,
    notes             TEXT
);
CREATE INDEX idx_incidents_started  ON incidents(started_at);
CREATE INDEX idx_incidents_camera   ON incidents(camera_id, started_at);
CREATE INDEX idx_incidents_status   ON incidents(status);
CREATE INDEX idx_incidents_severity ON incidents(severity);

CREATE TABLE incident_detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    frame_idx   INTEGER,
    class_name  TEXT NOT NULL,
    confidence  REAL NOT NULL,
    track_id    INTEGER,
    bbox        TEXT NOT NULL,                      -- JSON [x1, y1, x2, y2] in pixels
    threat_score INTEGER
);
CREATE INDEX idx_detections_incident ON incident_detections(incident_id);

CREATE TABLE evidence_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('snapshot', 'clip')),
    rel_path    TEXT NOT NULL UNIQUE,               -- relative to EVIDENCE_DIR
    sha256      TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    mime_type   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_evidence_incident ON evidence_files(incident_id);

CREATE TABLE alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT REFERENCES incidents(id) ON DELETE SET NULL,
    camera_id   TEXT,
    ts          TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    channel     TEXT NOT NULL,                      -- dashboard / webhook / log
    delivered   INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
CREATE INDEX idx_alerts_ts ON alerts(ts);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor       TEXT NOT NULL,                      -- username or "system"
    action      TEXT NOT NULL,                      -- e.g. incident.status_change
    entity_type TEXT,
    entity_id   TEXT,
    details     TEXT NOT NULL DEFAULT '{}',         -- JSON
    ip          TEXT
);
CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_ts     ON audit_log(ts);

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,                       -- JSON
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by TEXT
);
