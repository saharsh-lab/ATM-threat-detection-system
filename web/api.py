# web/api.py
# SentinelX REST API (all routes under /api). Every response that carries
# detections states its provenance: source_kind = live | recorded | demo.

import csv
import io
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, current_app, jsonify, request, send_file, stream_with_context
from werkzeug.exceptions import BadRequest, NotFound

import config
from core import db as dbm
from core import evidence, repository
from web import registry
from web.auth import client_ip, csrf_token, current_user, login, require

api = Blueprint("api", __name__)
_STARTED = time.time()
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?$")


def _ext():
    return current_app.extensions["sentinelx"]


def _db():
    return current_app.config["DB_PATH"]


def _actor():
    u = current_user()
    return u["username"] if u else "anonymous"


def _int_arg(name, default, lo, hi):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise BadRequest(f"{name} must be an integer") from None
    if not lo <= v <= hi:
        raise BadRequest(f"{name} must be between {lo} and {hi}")
    return v


def _list_arg(name, allowed):
    vals = [v for v in request.args.getlist(name) for v in v.split(",") if v]
    bad = [v for v in vals if v not in allowed]
    if bad:
        raise BadRequest(f"invalid {name}: {bad}")
    return vals


def _date_arg(name):
    v = request.args.get(name)
    if v and not ISO_RE.match(v):
        raise BadRequest(f"{name} must be an ISO-8601 date/time")
    return v


# ── auth ─────────────────────────────────────────────────────────────────

@api.post("/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    user, err = login(str(data.get("username", ""))[:64], str(data.get("password", ""))[:256])
    if err:
        return jsonify({"error": err}), 401 if "invalid" in err else 429
    return jsonify({"user": {"username": user["username"], "role": user["role"]}, "csrf": csrf_token()})


@api.post("/auth/logout")
@require("viewer")
def api_logout():
    from flask import session
    with dbm.connection(_db()) as conn:
        repository.audit(conn, _actor(), "auth.logout", "user", _actor(), {}, client_ip())
    session.clear()
    return jsonify({"ok": True})


@api.get("/auth/me")
def api_me():
    u = current_user()
    if not u:
        return jsonify({"user": None}), 401
    return jsonify({"user": {"username": u["username"], "role": u["role"]}, "csrf": csrf_token(),
                    "auth_disabled": current_app.config["AUTH_DISABLED"]})


# ── platform ─────────────────────────────────────────────────────────────

@api.get("/health")
def health():
    """Unauthenticated liveness/readiness (no sensitive data)."""
    try:
        db_ok = dbm.integrity_check(_db()) == "ok"
    except Exception:   # noqa: BLE001
        db_ok = False
    states = _ext()["cameras"].states()
    return jsonify({
        "status": "ok" if db_ok else "degraded", "mode": "live", "db": db_ok,
        "uptime_s": round(time.time() - _STARTED),
        "cameras": {"total": len(states), "streaming": sum(s["status"] in ("live", "replaying") for s in states),
                    "offline": sum(s["status"] in ("offline", "error") for s in states)},
    })


@api.get("/platform")
@require("viewer")
def platform_info():
    return jsonify({"mode": "live", "datasets": registry.datasets(), "models": registry.models(),
                    "settings": _ext()["settings"].describe()})


@api.get("/reports/<name>")
@require("viewer")
def get_report(name):
    rep = registry.reports().get(name)
    if rep is None:
        raise NotFound("report not generated yet")
    return jsonify(rep)


# ── cameras ──────────────────────────────────────────────────────────────

@api.get("/cameras")
@require("viewer")
def cameras():
    return jsonify({"cameras": _ext()["cameras"].states()})


def _worker(camera_id):
    w = _ext()["cameras"].get(camera_id)
    if w is None:
        raise NotFound("unknown or disabled camera")
    return w


@api.get("/cameras/<camera_id>/state")
@require("viewer")
def camera_state(camera_id):
    return jsonify(_worker(camera_id).snapshot_state())


@api.get("/cameras/<camera_id>/stream")
@require("viewer")
def camera_stream(camera_id):
    w = _worker(camera_id)
    interval = 1.0 / max(config.STREAM_FPS, 1)

    def gen():
        seq = -1
        while True:
            seq, jpeg = w.latest_jpeg(seq, timeout=2.0)
            if jpeg is None:
                time.sleep(0.2)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(interval)
    return Response(stream_with_context(gen()), mimetype="multipart/x-mixed-replace; boundary=frame")


@api.get("/cameras/<camera_id>/snapshot.jpg")
@require("viewer")
def camera_snapshot(camera_id):
    _, jpeg = _worker(camera_id).latest_jpeg(timeout=2.0)
    if jpeg is None:
        raise NotFound("no frame available yet")
    return Response(jpeg, mimetype="image/jpeg")


@api.post("/cameras/<camera_id>/video")
@require("analyst")
def camera_switch(camera_id):
    w = _worker(camera_id)
    name = str((request.get_json(silent=True) or {}).get("video", ""))
    try:
        w.switch_video(name)
    except FileNotFoundError:
        raise NotFound("video not found") from None
    except ValueError as e:
        raise BadRequest(str(e)) from None
    with dbm.connection(_db()) as conn:
        repository.audit(conn, _actor(), "camera.video_switched", "camera", camera_id, {"video": name}, client_ip())
    return jsonify({"ok": True, "video": name})


@api.get("/videos")
@require("viewer")
def videos():
    from core.camera import list_videos
    from data.annotations import get_video_info
    out = []
    for v in list_videos():
        info = get_video_info(v["name"])
        out.append({**v, "labelled": info["found"], "has_anomaly": info.get("has_anomaly")})
    return jsonify({"videos": out})


@api.post("/videos")
@require("analyst")
def upload_video():
    """Upload a recorded clip for analysis. Validated by extension, size and
    successful decoding; stored outside /static in the instance directory."""
    from core.camera import UPLOAD_DIR, VIDEO_EXTENSIONS, probe_video_file
    f = request.files.get("file")
    if f is None or not f.filename:
        raise BadRequest("multipart field 'file' is required")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise BadRequest(f"unsupported file type {ext!r}")
    os.makedirs(UPLOAD_DIR, mode=0o700, exist_ok=True)
    name = f"upload-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    f.save(path)
    if not probe_video_file(path):
        os.remove(path)
        raise BadRequest("file is not a decodable video")
    os.chmod(path, 0o600)
    with dbm.connection(_db()) as conn:
        repository.audit(conn, _actor(), "video.uploaded", "video", name,
                         {"original_name": f.filename[:200], "bytes": os.path.getsize(path)}, client_ip())
    return jsonify({"ok": True, "video": name}), 201


# ── incidents ────────────────────────────────────────────────────────────

def _incident_filters():
    return {
        "status": _list_arg("status", repository.INCIDENT_STATUSES),
        "severity": _list_arg("severity", repository.SEVERITIES),
        "source_kind": _list_arg("source", repository.SOURCE_KINDS),
        "camera_id": [c for c in request.args.getlist("camera") if re.fullmatch(r"[\w.-]{1,64}", c)],
        "q": (request.args.get("q") or "")[:200] or None,
        "since": _date_arg("since"), "until": _date_arg("until"),
        "min_score": _int_arg("min_score", None, 0, 100),
    }


@api.get("/incidents")
@require("viewer")
def incidents():
    res = repository.list_incidents(
        _incident_filters(), page=_int_arg("page", 1, 1, 100000), per_page=_int_arg("per_page", 25, 1, 200),
        sort=request.args.get("sort", "started_at"), order=request.args.get("order", "desc"), db=_db())
    return jsonify(res)


@api.get("/incidents/export.csv")
@require("analyst")
def incidents_export():
    res = repository.list_incidents(_incident_filters(), page=1, per_page=200, db=_db())
    buf = io.StringIO()
    cols = ["id", "camera_id", "source_kind", "started_at", "ended_at", "severity", "peak_score", "status",
            "primary_reason", "detection_classes", "max_confidence", "model_version", "engine_version", "video_ref"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in res["items"]:
        w.writerow([";".join(r[c]) if isinstance(r[c], list) else r[c] for c in cols])
    with dbm.connection(_db()) as conn:
        repository.audit(conn, _actor(), "incident.exported", "incident", None, {"rows": len(res["items"])},
                         client_ip())
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sentinelx-incidents.csv"})


@api.get("/incidents/<inc_id>")
@require("viewer")
def incident_detail(inc_id):
    inc = repository.get_incident(inc_id, db=_db())
    if inc is None:
        raise NotFound("incident not found")
    return jsonify(inc)


@api.patch("/incidents/<inc_id>")
@require("analyst")
def incident_update(inc_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        raise BadRequest("JSON body with status / notes / assigned_to required")
    try:
        inc = repository.update_incident(inc_id, data, _actor(), client_ip(), db=_db())
    except ValueError as e:
        raise BadRequest(str(e)) from None
    if inc is None:
        raise NotFound("incident not found")
    return jsonify(inc)


@api.get("/evidence/<int:evidence_id>")
@require("analyst")
def evidence_file(evidence_id):
    ev = repository.get_evidence(evidence_id, db=_db())
    if ev is None:
        raise NotFound("evidence not found")
    try:
        path = evidence.resolve(ev["rel_path"])
    except PermissionError:
        raise NotFound("evidence not found") from None
    if not os.path.isfile(path):
        raise NotFound("evidence file missing from storage")
    with dbm.connection(_db()) as conn:
        repository.audit(conn, _actor(), "evidence.viewed", "incident", ev["incident_id"],
                         {"evidence_id": evidence_id, "kind": ev["kind"]}, client_ip())
    resp = send_file(path, mimetype=ev["mime_type"], conditional=True, max_age=0,
                     download_name=os.path.basename(path), as_attachment=request.args.get("download") == "1")
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ── analytics / alerts ───────────────────────────────────────────────────

@api.get("/stats/summary")
@require("viewer")
def stats_summary():
    hours = _int_arg("hours", 24 * 7, 1, 24 * 365)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    return jsonify({"hours": hours, **repository.stats_summary(since, db=_db())})


@api.get("/alerts")
@require("viewer")
def alerts():
    return jsonify({"alerts": repository.list_alerts(_int_arg("since_id", 0, 0, 2**62), db=_db())})


# ── administration ───────────────────────────────────────────────────────

@api.get("/settings")
@require("viewer")
def get_settings():
    return jsonify({"settings": _ext()["settings"].describe()})


@api.put("/settings")
@require("admin")
def put_settings():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest("JSON object required")
    try:
        _ext()["settings"].update(data, _actor(), client_ip())
    except ValueError as e:
        raise BadRequest(str(e)) from None
    return jsonify({"settings": _ext()["settings"].describe()})


@api.get("/audit")
@require("admin")
def audit_log():
    return jsonify({"items": repository.list_audit(
        request.args.get("entity_type"), request.args.get("entity_id"),
        limit=_int_arg("limit", 100, 1, 500), offset=_int_arg("offset", 0, 0, 10**7), db=_db())})


@api.post("/admin/backup")
@require("admin")
def admin_backup():
    path = dbm.backup(_db(), label="api")
    with dbm.connection(_db()) as conn:
        repository.audit(conn, _actor(), "db.backup", "database", None, {"file": os.path.basename(path)}, client_ip())
    return jsonify({"ok": True, "file": os.path.basename(path)})
