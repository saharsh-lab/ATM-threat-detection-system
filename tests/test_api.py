import os

import config
from core import evidence, repository
from tests.conftest import login, make_user


def _incident(app):
    db = app.config["DB_PATH"]
    repository.upsert_camera({"id": "cam-01", "name": "A", "source_type": "file", "source_ref": "28.mp4"}, db=db)
    return repository.create_incident({"camera_id": "cam-01", "source_kind": "recorded", "threat_score": 70,
                                       "severity": "MEDIUM", "primary_reason": "Loitering Detected (25s)",
                                       "reasons": ["Loitering Detected (25s)"], "rules": ["loitering"]}, db=db)


def test_health_is_public_and_minimal(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["db"] is True and body["mode"] == "live" and "incidents" not in body


def test_everything_else_requires_login(client):
    for url in ("/api/incidents", "/api/cameras", "/api/stats/summary", "/api/platform", "/threat_data", "/api/evidence/1"):
        assert client.get(url).status_code == 401, url
    assert client.get("/").status_code == 302                     # → /login


def test_login_wrong_password_is_rejected_and_audited(app, client):
    make_user(app)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"})
    assert r.status_code == 401
    assert any(a["action"] == "auth.login_failed" for a in repository.list_audit(db=app.config["DB_PATH"]))


def test_login_throttle(app, client):
    make_user(app, "carol")
    for _ in range(8):
        client.post("/api/auth/login", json={"username": "carol", "password": "wrong-password-x"})
    r = client.post("/api/auth/login", json={"username": "carol", "password": "correct-horse-battery"})
    assert r.status_code == 429


def test_csrf_required_for_mutations(app, client):
    u, p = make_user(app)
    csrf = login(client, u, p)
    inc = _incident(app)
    assert client.patch(f"/api/incidents/{inc}", json={"status": "acknowledged"}).status_code == 403
    r = client.patch(f"/api/incidents/{inc}", json={"status": "acknowledged"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.get_json()["status"] == "acknowledged"


def test_viewer_cannot_edit_or_view_evidence(app, client):
    u, p = make_user(app, "victor", role="viewer")
    csrf = login(client, u, p)
    inc = _incident(app)
    assert client.get("/api/incidents").get_json()["total"] == 1
    assert client.patch(f"/api/incidents/{inc}", json={"status": "resolved"}, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert client.get("/api/evidence/1").status_code == 403
    assert client.put("/api/settings", json={"incident_open_score": 60}, headers={"X-CSRF-Token": csrf}).status_code == 403


def test_input_validation(app, client):
    u, p = make_user(app)
    csrf = login(client, u, p)
    inc = _incident(app)
    assert client.get("/api/incidents?severity=EXTREME").status_code == 400
    assert client.get("/api/incidents?per_page=10000").status_code == 400
    assert client.get("/api/incidents?since=yesterday").status_code == 400
    assert client.patch(f"/api/incidents/{inc}", json={"status": "gone"}, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert client.patch(f"/api/incidents/{inc}", json={"peak_score": 1}, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert client.patch("/api/incidents/INC-nope", json={"status": "resolved"}, headers={"X-CSRF-Token": csrf}).status_code == 404


def test_evidence_access_is_audited_and_private(app, client):
    u, p = make_user(app)
    login(client, u, p)
    inc = _incident(app)
    ev_id = evidence.save_snapshot("cam-01", inc, b"\xff\xd8\xffjpegbytes", db=app.config["DB_PATH"])
    r = client.get(f"/api/evidence/{ev_id}")
    assert r.status_code == 200 and r.data.startswith(b"\xff\xd8") and "no-store" in r.headers["Cache-Control"]
    assert any(a["action"] == "evidence.viewed" for a in repository.list_audit("incident", inc, db=app.config["DB_PATH"]))
    # tampered DB row pointing outside the evidence dir is refused
    with __import__("core.db", fromlist=["x"]).connection(app.config["DB_PATH"]) as c:
        c.execute("UPDATE evidence_files SET rel_path='../../../../etc/hosts' WHERE id=?", (ev_id,))
    assert client.get(f"/api/evidence/{ev_id}").status_code == 404
    assert not os.path.commonpath([os.path.realpath(config.EVIDENCE_DIR), os.path.realpath(config.BASE_DIR + "/static")]) == os.path.realpath(config.EVIDENCE_DIR)


def test_settings_validation_and_audit(app, client):
    u, p = make_user(app, "root", role="admin")
    csrf = login(client, u, p)
    h = {"X-CSRF-Token": csrf}
    assert client.put("/api/settings", json={"incident_open_score": 500}, headers=h).status_code == 400
    assert client.put("/api/settings", json={"alert_min_severity": "LOUD"}, headers=h).status_code == 400
    assert client.put("/api/settings", json={"unknown": 1}, headers=h).status_code == 400
    r = client.put("/api/settings", json={"incident_open_score": 60, "alert_min_severity": "CRITICAL"}, headers=h)
    vals = {s["key"]: s["value"] for s in r.get_json()["settings"]}
    assert vals["incident_open_score"] == 60 and vals["alert_min_severity"] == "CRITICAL"
    assert client.get("/api/audit").status_code == 200


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_switch_video_rejects_traversal(app, client):
    from core.camera import resolve_video
    import pytest
    for bad in ("../app.py", "/etc/passwd", "..", ".hidden.mp4", "x.exe"):
        with pytest.raises((ValueError, FileNotFoundError)):
            resolve_video(bad)
    assert resolve_video("28.mp4").endswith("data/videos/28.mp4")
