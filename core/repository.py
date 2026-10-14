# core/repository.py
# Data-access layer for incidents, evidence, alerts, audit log, users and
# settings. All SQL lives here; every query is parameterised.

import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from core.db import connection, transaction, utcnow

INCIDENT_STATUSES = ("open", "acknowledged", "investigating", "resolved", "false_positive")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ROLES = ("viewer", "analyst", "admin")
SOURCE_KINDS = ("live", "recorded", "demo")
SORTABLE = {"started_at", "peak_score", "severity", "status", "camera_id", "updated_at"}

_J = json.dumps


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("reasons", "rules", "detection_classes", "details", "zone_config", "stats", "metrics", "bbox", "value"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def new_incident_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"INC-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


# ── audit ────────────────────────────────────────────────────────────────

def audit(conn, actor: str, action: str, entity_type: str | None = None,
          entity_id: str | None = None, details: dict | None = None, ip: str | None = None):
    conn.execute("INSERT INTO audit_log (ts, actor, action, entity_type, entity_id, details, ip) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (utcnow(), actor, action, entity_type, entity_id, _J(details or {}), ip))


def list_audit(entity_type=None, entity_id=None, limit=100, offset=0, db=None):
    where, args = [], []
    if entity_type:
        where.append("entity_type = ?"); args.append(entity_type)
    if entity_id:
        where.append("entity_id = ?"); args.append(entity_id)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    with connection(db) as conn:
        return [_row(r) for r in conn.execute(sql, (*args, int(limit), int(offset)))]


# ── registry tables ──────────────────────────────────────────────────────

def upsert_camera(cam: dict, db=None):
    with connection(db) as conn:
        conn.execute(
            """INSERT INTO cameras (id, name, location, source_type, source_ref, zone_config, enabled)
               VALUES (:id, :name, :location, :source_type, :source_ref, :zone_config, :enabled)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, location=excluded.location,
                 source_type=excluded.source_type, source_ref=excluded.source_ref,
                 zone_config=excluded.zone_config, enabled=excluded.enabled,
                 updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            {**cam, "zone_config": _J(cam.get("zone_config", {})), "enabled": int(cam.get("enabled", True)),
             "location": cam.get("location")})


def upsert_dataset(ds: dict, db=None):
    with connection(db) as conn:
        conn.execute(
            """INSERT INTO datasets (id, name, version, label_type, source_url, license, access, manifest_path, stats)
               VALUES (:id, :name, :version, :label_type, :source_url, :license, :access, :manifest_path, :stats)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, version=excluded.version,
                 label_type=excluded.label_type, source_url=excluded.source_url, license=excluded.license,
                 access=excluded.access, manifest_path=excluded.manifest_path, stats=excluded.stats""",
            {"version": None, "source_url": None, "license": None, "access": None, "manifest_path": None,
             **ds, "stats": _J(ds.get("stats", {}))})


def upsert_model(m: dict, db=None):
    with connection(db) as conn:
        conn.execute(
            """INSERT INTO model_versions (id, name, weights_path, weights_sha256, class_schema, dataset_id,
                                           engine_version, metrics)
               VALUES (:id, :name, :weights_path, :weights_sha256, :class_schema, :dataset_id,
                       :engine_version, :metrics)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, weights_path=excluded.weights_path,
                 weights_sha256=excluded.weights_sha256, class_schema=excluded.class_schema,
                 dataset_id=excluded.dataset_id, engine_version=excluded.engine_version,
                 metrics=excluded.metrics""",
            {"weights_sha256": None, "dataset_id": None, "engine_version": None,
             **m, "metrics": _J(m.get("metrics", {}))})


def list_table(table: str, db=None):
    if table not in ("cameras", "datasets", "model_versions"):
        raise ValueError(table)
    with connection(db) as conn:
        return [_row(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


# ── incidents ────────────────────────────────────────────────────────────

def create_incident(inc: dict, db=None) -> str:
    now = utcnow()
    inc_id = inc.get("id") or new_incident_id()
    with connection(db) as conn, transaction(conn):
        conn.execute(
            """INSERT INTO incidents (id, camera_id, source_kind, video_ref, started_at, updated_at,
                 frame_start, frame_end, media_time_start, media_time_end, threat_score, peak_score, severity,
                 status, primary_reason, reasons, rules, detection_classes, max_confidence,
                 model_version, engine_version, dataset_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (inc_id, inc["camera_id"], inc["source_kind"], inc.get("video_ref"), inc.get("started_at", now), now,
             inc.get("frame_start"), inc.get("frame_end"), inc.get("media_time_start"), inc.get("media_time_end"),
             inc["threat_score"], inc["threat_score"], inc["severity"], inc.get("primary_reason"),
             _J(inc.get("reasons", [])), _J(inc.get("rules", [])), _J(inc.get("detection_classes", [])),
             inc.get("max_confidence"), inc.get("model_version"), inc.get("engine_version"),
             inc.get("dataset_id")))
        audit(conn, "system", "incident.opened", "incident", inc_id,
              {"camera_id": inc["camera_id"], "severity": inc["severity"], "score": inc["threat_score"],
               "source_kind": inc["source_kind"]})
    return inc_id


def update_incident_progress(inc_id: str, upd: dict, db=None):
    """Called by the incident manager while an incident is active."""
    with connection(db) as conn:
        conn.execute(
            """UPDATE incidents SET updated_at=?, threat_score=?, peak_score=max(peak_score, ?),
                 severity=?, frame_end=coalesce(?, frame_end), media_time_end=coalesce(?, media_time_end),
                 reasons=?, rules=?, detection_classes=?,
                 max_confidence=max(coalesce(max_confidence, 0), coalesce(?, 0)),
                 ended_at=coalesce(?, ended_at)
               WHERE id=?""",
            (utcnow(), upd["threat_score"], upd["threat_score"], upd["severity"], upd.get("frame_end"),
             upd.get("media_time_end"), _J(upd.get("reasons", [])), _J(upd.get("rules", [])),
             _J(upd.get("detection_classes", [])), upd.get("max_confidence"), upd.get("ended_at"), inc_id))
        if upd.get("ended_at"):
            audit(conn, "system", "incident.closed", "incident", inc_id, {"peak_severity": upd["severity"]})


def add_detections(inc_id: str, detections: list[dict], ts: str, frame_idx: int | None, score: int, db=None):
    if not detections:
        return
    with connection(db) as conn, transaction(conn):
        conn.executemany(
            "INSERT INTO incident_detections (incident_id, ts, frame_idx, class_name, confidence, track_id, bbox, "
            "threat_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(inc_id, ts, frame_idx, d["class_name"], float(d["confidence"]), d.get("track_id"),
              _J([int(v) for v in d["bbox"]]), score) for d in detections])


def add_evidence(inc_id: str, kind: str, rel_path: str, sha256: str, size_bytes: int, mime_type: str, db=None) -> int:
    with connection(db) as conn:
        cur = conn.execute(
            "INSERT INTO evidence_files (incident_id, kind, rel_path, sha256, size_bytes, mime_type) "
            "VALUES (?, ?, ?, ?, ?, ?)", (inc_id, kind, rel_path, sha256, size_bytes, mime_type))
        return cur.lastrowid


def get_evidence(evidence_id: int, db=None):
    with connection(db) as conn:
        return _row(conn.execute("SELECT * FROM evidence_files WHERE id=?", (evidence_id,)).fetchone())


def list_expired_evidence(older_than_days: int, db=None):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%S")
    with connection(db) as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM evidence_files WHERE created_at < ?", (cutoff,))]


def delete_evidence_row(evidence_id: int, actor: str, db=None):
    with connection(db) as conn, transaction(conn):
        conn.execute("DELETE FROM evidence_files WHERE id=?", (evidence_id,))
        audit(conn, actor, "evidence.purged", "evidence", str(evidence_id))


def _fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 prefix query ("atm" "rob"*)."""
    terms = re.findall(r"[\w-]+", q)[:8]
    return " ".join(f'"{t}"*' for t in terms)


def list_incidents(filters: dict | None = None, page: int = 1, per_page: int = 25,
                   sort: str = "started_at", order: str = "desc", db=None) -> dict:
    f = filters or {}
    where, args = [], []
    for key, col in (("status", "status"), ("severity", "severity"), ("camera_id", "camera_id"),
                     ("source_kind", "source_kind"), ("model_version", "model_version")):
        vals = f.get(key)
        if vals:
            vals = [vals] if isinstance(vals, str) else list(vals)
            where.append(f"i.{col} IN ({','.join('?' * len(vals))})")
            args.extend(vals)
    if f.get("since"):
        where.append("i.started_at >= ?"); args.append(f["since"])
    if f.get("until"):
        where.append("i.started_at <= ?"); args.append(f["until"])
    if f.get("min_score") is not None:
        where.append("i.peak_score >= ?"); args.append(int(f["min_score"]))
    if f.get("q"):
        q = f["q"].strip()
        fts = _fts_query(q)
        clauses = ["i.id LIKE ?"]
        args_q = [f"%{q}%"]
        if fts:
            clauses.append("i.id IN (SELECT incident_id FROM incident_search WHERE incident_search MATCH ?)")
            args_q.append(fts)
        where.append("(" + " OR ".join(clauses) + ")")
        args.extend(args_q)
    sort = sort if sort in SORTABLE else "started_at"
    order = "ASC" if str(order).lower() == "asc" else "DESC"
    sort_expr = ("CASE i.severity WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 ELSE 1 END"
                 if sort == "severity" else f"i.{sort}")
    per_page = max(1, min(int(per_page), 200))
    page = max(1, int(page))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with connection(db) as conn:
        total = conn.execute(f"SELECT count(*) FROM incidents i{clause}", args).fetchone()[0]
        rows = conn.execute(
            f"""SELECT i.*, (SELECT count(*) FROM evidence_files e WHERE e.incident_id = i.id) AS evidence_count
                FROM incidents i{clause} ORDER BY {sort_expr} {order}, i.id DESC LIMIT ? OFFSET ?""",
            (*args, per_page, (page - 1) * per_page)).fetchall()
    return {"items": [_row(r) for r in rows], "total": total, "page": page, "per_page": per_page}


def get_incident(inc_id: str, db=None):
    with connection(db) as conn:
        inc = _row(conn.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone())
        if inc is None:
            return None
        inc["detections"] = [_row(r) for r in conn.execute(
            "SELECT * FROM incident_detections WHERE incident_id=? ORDER BY id LIMIT 500", (inc_id,))]
        inc["evidence"] = [_row(r) for r in conn.execute(
            "SELECT id, kind, sha256, size_bytes, mime_type, created_at FROM evidence_files "
            "WHERE incident_id=? ORDER BY id", (inc_id,))]
        inc["audit"] = [_row(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE entity_type='incident' AND entity_id=? ORDER BY id DESC LIMIT 100",
            (inc_id,))]
    return inc


def update_incident(inc_id: str, changes: dict, actor: str, ip: str | None = None, db=None):
    """Analyst edits: status / notes / assigned_to. Every change is audited."""
    allowed = {"status", "notes", "assigned_to"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"fields not editable: {sorted(unknown)}")
    if "status" in changes and changes["status"] not in INCIDENT_STATUSES:
        raise ValueError(f"invalid status {changes['status']!r}")
    for k in ("notes", "assigned_to"):
        if k in changes and changes[k] is not None:
            changes[k] = str(changes[k])[:4000 if k == "notes" else 120]
    with connection(db) as conn, transaction(conn):
        cur = conn.execute("SELECT status, notes, assigned_to FROM incidents WHERE id=?", (inc_id,)).fetchone()
        if cur is None:
            return None
        diff = {k: {"from": cur[k], "to": v} for k, v in changes.items() if cur[k] != v}
        if diff:
            sets = ", ".join(f"{k}=?" for k in diff)
            conn.execute(f"UPDATE incidents SET {sets}, updated_at=? WHERE id=?",
                         (*[changes[k] for k in diff], utcnow(), inc_id))
            action = "incident.status_change" if "status" in diff else "incident.updated"
            audit(conn, actor, action, "incident", inc_id, diff, ip)
    return get_incident(inc_id, db)


def stats_summary(since: str | None = None, db=None) -> dict:
    where = "WHERE started_at >= ?" if since else ""
    args = (since,) if since else ()
    with connection(db) as conn:
        def group(col):
            return {r[0]: r[1] for r in conn.execute(
                f"SELECT {col}, count(*) FROM incidents {where} GROUP BY {col}", args)}
        hourly = [dict(r) for r in conn.execute(
            f"""SELECT substr(started_at, 1, 13) AS hour, count(*) AS n, max(peak_score) AS peak
                FROM incidents {where} GROUP BY hour ORDER BY hour""", args)]
        rules = {}
        for (rj,) in conn.execute(f"SELECT rules FROM incidents {where}", args):
            for r in json.loads(rj or "[]"):
                rules[r] = rules.get(r, 0) + 1
        totals = conn.execute(
            f"SELECT count(*), avg(peak_score), sum(status='open') FROM incidents {where}", args).fetchone()
        return {"total": totals[0] or 0, "avg_peak_score": round(totals[1] or 0, 1), "open": totals[2] or 0,
                "by_severity": group("severity"), "by_status": group("status"), "by_camera": group("camera_id"),
                "by_source": group("source_kind"), "by_rule": rules, "hourly": hourly}


# ── alerts ───────────────────────────────────────────────────────────────

def add_alert(alert: dict, db=None) -> int:
    with connection(db) as conn:
        cur = conn.execute(
            "INSERT INTO alerts (incident_id, camera_id, ts, severity, message, channel, delivered, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (alert.get("incident_id"), alert.get("camera_id"), alert.get("ts", utcnow()), alert["severity"],
             alert["message"], alert["channel"], int(alert.get("delivered", 0)), alert.get("error")))
        return cur.lastrowid


def list_alerts(since_id: int = 0, limit: int = 50, db=None):
    with connection(db) as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM alerts WHERE id > ? AND channel='dashboard' ORDER BY id DESC LIMIT ?",
            (int(since_id), int(limit)))]


# ── users ────────────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str, role: str, actor: str = "system", db=None) -> int:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        raise ValueError("username must be 3-64 chars of letters, digits, . _ -")
    with connection(db) as conn, transaction(conn):
        cur = conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                           (username, password_hash, role))
        audit(conn, actor, "user.created", "user", username, {"role": role})
        return cur.lastrowid


def get_user(username: str, db=None):
    with connection(db) as conn:
        return _row(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())


def get_user_by_id(user_id: int, db=None):
    with connection(db) as conn:
        return _row(conn.execute("SELECT id, username, role, disabled FROM users WHERE id=?",
                                 (user_id,)).fetchone())


def count_users(db=None) -> int:
    with connection(db) as conn:
        return conn.execute("SELECT count(*) FROM users").fetchone()[0]


def record_login(user_id: int, username: str, ip: str | None, success: bool, db=None):
    with connection(db) as conn:
        if success:
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (utcnow(), user_id))
        audit(conn, username, "auth.login" if success else "auth.login_failed", "user", username, {}, ip)


# ── settings ─────────────────────────────────────────────────────────────

def get_settings(db=None) -> dict:
    with connection(db) as conn:
        return {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}


def set_settings(values: dict, actor: str, ip: str | None = None, db=None):
    with connection(db) as conn, transaction(conn):
        old = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}
        for k, v in values.items():
            conn.execute("INSERT INTO settings (key, value, updated_by) VALUES (?, ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, "
                         "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')", (k, _J(v), actor))
        audit(conn, actor, "settings.updated", "settings", None,
              {k: {"from": old.get(k), "to": v} for k, v in values.items() if old.get(k) != v}, ip)
