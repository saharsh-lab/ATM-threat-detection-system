import pytest

from core import db, repository


@pytest.fixture
def cam(db_path):
    repository.upsert_camera({"id": "cam-01", "name": "A", "source_type": "file", "source_ref": "28.mp4",
                              "zone_config": {}}, db=db_path)
    return db_path


def _inc(db_path, **kw):
    base = {"camera_id": "cam-01", "source_kind": "recorded", "threat_score": 60, "severity": "MEDIUM",
            "primary_reason": "Loitering Detected (21s)", "reasons": ["Loitering Detected (21s)"],
            "rules": ["loitering"], "detection_classes": ["person"]}
    base.update(kw)
    return repository.create_incident(base, db=db_path)


def test_create_and_get_incident_with_audit(cam):
    inc_id = _inc(cam)
    assert inc_id.startswith("INC-")
    inc = repository.get_incident(inc_id, db=cam)
    assert inc["status"] == "open" and inc["peak_score"] == 60
    assert inc["reasons"] == ["Loitering Detected (21s)"]
    assert inc["audit"][0]["action"] == "incident.opened"


def test_progress_keeps_peak_and_closes(cam):
    inc_id = _inc(cam)
    repository.update_incident_progress(inc_id, {"threat_score": 90, "severity": "CRITICAL"}, db=cam)
    repository.update_incident_progress(inc_id, {"threat_score": 30, "severity": "CRITICAL",
                                                 "ended_at": db.utcnow()}, db=cam)
    inc = repository.get_incident(inc_id, db=cam)
    assert inc["peak_score"] == 90 and inc["threat_score"] == 30 and inc["ended_at"]
    assert any(a["action"] == "incident.closed" for a in inc["audit"])


def test_update_validates_and_audits_diff(cam):
    inc_id = _inc(cam)
    with pytest.raises(ValueError):
        repository.update_incident(inc_id, {"status": "deleted"}, "bob", db=cam)
    with pytest.raises(ValueError):
        repository.update_incident(inc_id, {"severity": "LOW"}, "bob", db=cam)   # not editable
    inc = repository.update_incident(inc_id, {"status": "false_positive", "notes": "shadow"}, "bob", "1.2.3.4", db=cam)
    assert inc["status"] == "false_positive"
    change = next(a for a in inc["audit"] if a["action"] == "incident.status_change")
    assert change["actor"] == "bob" and change["details"]["status"] == {"from": "open", "to": "false_positive"}
    assert repository.update_incident("INC-missing", {"status": "resolved"}, "bob", db=cam) is None


def test_filters_search_and_pagination(cam):
    a = _inc(cam, severity="HIGH", threat_score=80, primary_reason="Weapon Detected: knife",
             reasons=["Weapon Detected: knife"], detection_classes=["person", "knife"])
    _inc(cam)
    _inc(cam, source_kind="live")
    assert repository.list_incidents({"severity": ["HIGH"]}, db=cam)["total"] == 1
    assert repository.list_incidents({"source_kind": ["live"]}, db=cam)["total"] == 1
    hits = repository.list_incidents({"q": "knife"}, db=cam)
    assert [i["id"] for i in hits["items"]] == [a]
    assert repository.list_incidents({"q": "kni"}, db=cam)["total"] == 1          # prefix search
    assert repository.list_incidents({"q": '"; DROP TABLE incidents; --'}, db=cam)["total"] == 0
    page = repository.list_incidents({}, page=2, per_page=2, db=cam)
    assert page["total"] == 3 and len(page["items"]) == 1
    srt = repository.list_incidents({}, sort="severity", order="desc", db=cam)
    assert srt["items"][0]["severity"] == "HIGH"


def test_stats_summary(cam):
    _inc(cam, severity="HIGH")
    _inc(cam)
    s = repository.stats_summary(db=cam)
    assert s["total"] == 2 and s["by_severity"] == {"HIGH": 1, "MEDIUM": 1} and s["by_rule"]["loitering"] == 2


def test_users_and_settings(db_path):
    repository.create_user("alice", "hash", "admin", db=db_path)
    with pytest.raises(ValueError):
        repository.create_user("x", "hash", "admin", db=db_path)                # too short
    with pytest.raises(ValueError):
        repository.create_user("bob", "hash", "root", db=db_path)               # bad role
    assert repository.get_user("ALICE", db=db_path)["role"] == "admin"          # case-insensitive
    repository.set_settings({"incident_open_score": 60}, "alice", db=db_path)
    assert repository.get_settings(db=db_path) == {"incident_open_score": 60}
    assert repository.list_audit("settings", db=db_path)[0]["details"]["incident_open_score"]["to"] == 60
