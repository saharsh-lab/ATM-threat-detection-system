import os

import numpy as np
import pytest

import config
from core import repository
from core.alerts import AlertManager
from core.incidents import IncidentManager, encode_jpeg
from tests.conftest import Clock, FakeSettings


def threat(score, level, rules=()):
    return {"score": score, "level": level, "reasons": [f"{r} fired" for r in rules],
            "signals": [{"rule": r, "weight": 10, "message": f"{r} fired"} for r in rules]}


# ── alerts ────────────────────────────────────────────────────────────────

def test_alert_severity_gate_cooldown_escalation_and_new_rules(db_path):
    clock = Clock()
    am = AlertManager(FakeSettings(alert_min_severity="HIGH", alert_cooldown_seconds=60), db=db_path, clock=clock)
    assert not am.should_alert("c", "MEDIUM", {"loitering"})                # below gate
    assert am.should_alert("c", "HIGH", {"weapon"})
    assert not am.should_alert("c", "HIGH", {"weapon"})                     # duplicate in cooldown
    assert am.should_alert("c", "CRITICAL", {"weapon"})                     # escalation bypasses
    assert not am.should_alert("c", "HIGH", {"weapon"})                     # lower than last
    assert am.should_alert("c", "HIGH", {"weapon", "firearm"})              # new rule
    assert am.should_alert("other-cam", "HIGH", {"weapon"})                 # per camera
    clock.t += 61
    assert am.should_alert("c", "HIGH", {"weapon"})                         # cooldown expired
    assert am.suppressed == 2


def test_dispatch_records_dashboard_alert_and_tags_source(db_path):
    am = AlertManager(FakeSettings(alert_min_severity="HIGH"), db=db_path)
    assert am.dispatch(None, "cam-01", "Vestibule A", "HIGH", 80, ["Weapon Detected: knife"], {"weapon"}, "recorded")
    alerts = repository.list_alerts(db=db_path)
    assert len(alerts) == 1 and "[RECORDED]" in alerts[0]["message"] and alerts[0]["delivered"] == 1


# ── incident lifecycle ────────────────────────────────────────────────────

@pytest.fixture
def mgr(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVIDENCE_DIR", str(tmp_path / "evidence"))
    os.makedirs(config.EVIDENCE_DIR)
    repository.upsert_camera({"id": "cam-01", "name": "A", "source_type": "file", "source_ref": "28.mp4"}, db=db_path)
    repository.upsert_model({"id": "yolov8n-coco", "name": "baseline", "weights_path": "yolov8n.pt",
                             "class_schema": "coco"}, db=db_path)
    clock = Clock()
    settings = FakeSettings(incident_open_score=50, incident_close_seconds=5, evidence_clip_seconds=0,
                            alert_min_severity="HIGH")
    cam = {"id": "cam-01", "name": "A", "source_type": "file", "video_ref": "28.mp4"}
    m = IncidentManager(cam, settings, AlertManager(settings, db=db_path, clock=clock),
                        {"id": "yolov8n-coco"}, "rules-v2", db=db_path, clock=clock)
    return m, clock, db_path


def test_incident_opens_escalates_and_closes(mgr):
    m, clock, db = mgr
    jpeg = encode_jpeg(np.zeros((48, 64, 3), np.uint8))
    m.update(threat(30, "LOW", ["loitering"]), [], 1, 0.0, jpeg)
    assert m.active_id is None                                               # below threshold
    m.update(threat(60, "MEDIUM", ["loitering"]), [{"class_name": "person", "confidence": .9, "bbox": (1, 2, 3, 4)}], 10, 1.0, jpeg)
    inc_id = m.active_id
    assert inc_id
    clock.t += 1
    m.update(threat(95, "CRITICAL", ["weapon"]), [{"class_name": "knife", "confidence": .8, "bbox": (1, 2, 3, 4)}], 20, 2.0, jpeg)
    clock.t += 1
    m.update(threat(20, "NORMAL"), [], 30, 3.0, jpeg)                        # below, not yet closed
    assert m.active_id == inc_id
    clock.t += 6
    m.update(threat(10, "NORMAL"), [], 40, 9.0, jpeg)                        # closed after 5 s below
    assert m.active_id is None

    inc = repository.get_incident(inc_id, db=db)
    assert inc["severity"] == "CRITICAL" and inc["peak_score"] == 95 and inc["ended_at"]
    assert inc["source_kind"] == "recorded" and inc["model_version"] == "yolov8n-coco"
    assert set(inc["rules"]) == {"loitering", "weapon"} and "knife" in inc["detection_classes"]
    assert inc["max_confidence"] == pytest.approx(0.8)
    assert [e["kind"] for e in inc["evidence"]] == ["snapshot", "snapshot"]  # open + escalation
    assert inc["detections"]
    alerts = repository.list_alerts(db=db)
    assert len(alerts) == 1 and alerts[0]["severity"] == "CRITICAL"          # MEDIUM below alert gate


def test_evidence_files_are_private_and_hash_verified(mgr):
    import hashlib
    from core import evidence
    m, clock, db = mgr
    m.update(threat(60, "MEDIUM", ["loitering"]), [], 1, 0.0, b"\xff\xd8fakejpeg")
    ev = repository.get_incident(m.active_id, db=db)["evidence"][0]
    row = repository.get_evidence(ev["id"], db=db)
    path = evidence.resolve(row["rel_path"])
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == row["sha256"]
    with pytest.raises(PermissionError):
        evidence.resolve("../../etc/passwd")


def test_db_failure_never_breaks_pipeline(mgr, monkeypatch):
    m, clock, db = mgr
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(repository, "create_incident", boom)
    m.update(threat(80, "HIGH", ["weapon"]), [], 1, 0.0)                     # must not raise
    assert m.active_id is None
