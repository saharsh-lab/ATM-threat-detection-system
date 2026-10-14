import pytest

import config
from core.threat_engine import ThreatEngine, classify_risk, severity_at_least
from core.tracker import PersonTracker
from core.zones import ZoneConfig, point_in_polygon, validate_polygon

W, H = 1000, 1000


def person(bbox, conf=0.9, tid=None):
    return {"class_name": "person", "confidence": conf, "bbox": bbox, "track_id": tid}


def obj(name, bbox=(480, 480, 520, 520), conf=0.8):
    return {"class_name": name, "confidence": conf, "bbox": bbox}


# ── zones ─────────────────────────────────────────────────────────────────

def test_point_in_polygon_and_validation():
    sq = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]
    assert point_in_polygon(0.5, 0.5, sq) and not point_in_polygon(0.1, 0.5, sq)
    with pytest.raises(ValueError):
        validate_polygon([(0, 0), (1, 1)])
    with pytest.raises(ValueError):
        validate_polygon([(0, 0), (1.2, 0), (1, 1)])
    z = ZoneConfig.from_dict({"atm_zone": sq, "loitering_seconds": 5})
    assert z.in_atm_zone((450, 300, 550, 700), W, H)
    assert not z.in_atm_zone((0, 0, 100, 150), W, H)


# ── risk levels ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,level", [(0, "NORMAL"), (24, "NORMAL"), (25, "LOW"), (50, "MEDIUM"), (75, "HIGH"), (90, "CRITICAL"), (100, "CRITICAL")])
def test_classify_risk(score, level):
    assert classify_risk(score) == level


def test_severity_order():
    assert severity_at_least("CRITICAL", "HIGH") and not severity_at_least("MEDIUM", "HIGH")


# ── tracker ───────────────────────────────────────────────────────────────

def test_tracker_uses_external_ids_and_accumulates_zone_dwell_only_in_zone():
    zones = ZoneConfig.from_dict({"atm_zone": [(0.3, 0.3), (0.7, 0.3), (0.7, 0.9), (0.3, 0.9)], "loitering_seconds": 3})
    tr = PersonTracker(zones)
    inside, outside = (420, 300, 580, 800), (0, 300, 120, 800)
    for i in range(41):                                   # 4 s at 10 fps inside the zone
        out = tr.update([person(inside, tid=7), person(outside, tid=8)], W, H, now=i * 0.1)
    p7 = next(p for p in out if p["track_id"] == 7)
    p8 = next(p for p in out if p["track_id"] == 8)
    assert p7["dwell_sec"] == pytest.approx(4.0, abs=0.11) and p7["loitering"]
    assert p8["dwell_sec"] == 0 and not p8["loitering"] and not p8["in_zone"]


def test_tracker_iou_fallback_keeps_identity():
    tr = PersonTracker()
    a = tr.update([person((100, 100, 200, 400))], W, H, now=0)[0]["track_id"]
    b = tr.update([person((110, 105, 210, 405))], W, H, now=0.1)[0]["track_id"]
    assert a == b


def test_tracker_rapid_motion_requires_sustained_speed():
    tr = PersonTracker()
    out = None
    for i in range(25):                                    # moves 0.3 bbox heights per 0.1 s = 3 h/s
        x = 300 + (90 if i % 2 else 0)
        out = tr.update([person((x, 300, x + 150, 600), tid=1)], W, H, now=i * 0.1)
    assert out[0]["rapid_motion"]
    tr.reset()
    for i in range(25):                                    # standing still
        out = tr.update([person((300, 300, 450, 600), tid=1)], W, H, now=i * 0.1)
    assert not out[0]["rapid_motion"]


def test_stale_tracks_expire():
    tr = PersonTracker()
    tr.update([person((0, 0, 100, 300), tid=1)], W, H, now=0)
    tr.update([], W, H, now=config.TRACK_EXPIRY_SECONDS + 1)
    assert tr._tracks == {}


# ── threat engine ─────────────────────────────────────────────────────────

def run(engine, frames, dt=0.1, t0=0.0):
    res = None
    for i, (tracked, dets) in enumerate(frames):
        res = engine.evaluate(tracked, dets, W, H, now=t0 + i * dt)
    return res


def test_single_frame_weapon_does_not_fire_but_persistent_one_does():
    e = ThreatEngine()
    res = run(e, [([], [obj("knife")])] + [([], [])] * 3)
    assert "weapon" not in {s["rule"] for s in res["signals"]}
    e.reset()
    res = run(e, [([], [obj("knife")])] * config.OBJECT_PERSISTENCE_MIN_HITS)
    assert "weapon" in {s["rule"] for s in res["signals"]}
    assert res["raw_score"] == config.SCORE_WEAPON_OBJECT


def test_low_confidence_detections_are_ignored():
    e = ThreatEngine()
    res = run(e, [([], [obj("firearm", conf=0.2)])] * 10)
    assert res["raw_score"] == 0


def test_multiple_persons_counts_only_zone_occupants():
    e = ThreatEngine()
    p1 = {**person((100, 100, 200, 400)), "track_id": 1, "in_zone": True}
    p2 = {**person((600, 100, 700, 400)), "track_id": 2, "in_zone": False}
    assert "multiple_persons" not in {s["rule"] for s in e.evaluate([p1, p2], [p1, p2], W, H, now=0)["signals"]}
    p2["in_zone"] = True
    assert "multiple_persons" in {s["rule"] for s in e.evaluate([p1, p2], [p1, p2], W, H, now=0.1)["signals"]}


def test_close_proximity_flags_person_next_to_user():
    e = ThreatEngine()
    p1 = {**person((400, 200, 500, 600)), "track_id": 1, "in_zone": True}
    p2 = {**person((470, 200, 570, 600)), "track_id": 2, "in_zone": True}
    rules = {s["rule"] for s in e.evaluate([p1, p2], [p1, p2], W, H, now=0)["signals"]}
    assert {"multiple_persons", "close_proximity"} <= rules


def test_helmet_only_counts_when_worn_in_zone():
    e = ThreatEngine()
    p = {**person((400, 200, 500, 600)), "track_id": 1, "in_zone": True}
    worn = obj("helmet", (410, 190, 490, 260))
    carried = obj("helmet", (420, 500, 480, 560))
    assert "face_concealment" not in {s["rule"] for s in run(e, [([p], [p, carried])] * 5)["signals"]}
    e.reset()
    assert "face_concealment" in {s["rule"] for s in run(e, [([p], [p, worn])] * 5)["signals"]}


def test_unattended_bag_needs_time_and_absence():
    e = ThreatEngine()
    bag = obj("backpack", (800, 800, 860, 880))
    res = run(e, [([], [bag])] * 50, dt=0.1)                     # 5 s < threshold
    assert "unattended_bag" not in {s["rule"] for s in res["signals"]}
    res = run(e, [([], [bag])] * 80, dt=0.1, t0=5.0)             # total > 10 s
    assert "unattended_bag" in {s["rule"] for s in res["signals"]}


def test_score_rises_holds_then_decays_time_based():
    e = ThreatEngine()
    gun = obj("firearm")
    res = run(e, [([], [gun])] * 30)                             # 3 s of firearm
    assert res["score"] == config.SCORE_FIREARM
    held = run(e, [([], [])] * 20, t0=3.0)                       # 2 s gap < hold time
    assert held["score"] == config.SCORE_FIREARM
    decayed = run(e, [([], [])] * 60, t0=5.0)                    # well past hold → decays
    assert decayed["score"] < config.SCORE_FIREARM
    gone = run(e, [([], [])] * 200, t0=11.0)
    assert gone["score"] == 0 and gone["level"] == "NORMAL"


def test_score_is_capped_at_100():
    e = ThreatEngine()
    p1 = {**person((400, 200, 500, 600)), "track_id": 1, "in_zone": True, "loitering": True, "dwell_sec": 30,
          "vandalism": True, "vandalism_duration": 8, "rapid_motion": True, "speed": 3}
    p2 = {**person((470, 200, 570, 600)), "track_id": 2, "in_zone": True}
    dets = [p1, p2, obj("knife"), obj("firearm")]
    res = run(e, [([p1, p2], dets)] * 40)
    assert res["raw_score"] == 100 and res["score"] == 100 and res["level"] == "CRITICAL"
