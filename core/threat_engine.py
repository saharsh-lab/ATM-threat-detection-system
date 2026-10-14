# core/threat_engine.py
# Rule-based, temporally smoothed threat scoring engine (v2).
#
# Each rule emits a "signal" (rule id, weight, message, evidence). The raw
# score is the capped sum of active signal weights. The published score rises
# quickly toward the raw score, holds for SCORE_HOLD_SECONDS after threats
# clear and then decays — expressed per second so it is FPS-independent.
#
# Object rules (weapons, tools, bottles, helmets) require temporal persistence:
# the object must be seen in OBJECT_PERSISTENCE_MIN_HITS detections inside the
# last OBJECT_PERSISTENCE_WINDOW_SECONDS. Single-frame false positives —
# the main failure mode of the v0 engine — therefore never raise the score.

import time
from collections import defaultdict, deque

import config
from core.zones import ZoneConfig

ENGINE_VERSION = "rules-v2"

BAG_CLASSES     = {"backpack", "handbag", "suitcase"}
BLADED_CLASSES  = {"knife", "scissors"}
FIREARM_CLASSES = {"firearm"}
BLUNT_CLASSES   = {"blunt_tool"}
OBJECT_CLASSES  = {"bottle"}
HELMET_CLASSES  = {"helmet"}


def classify_risk(score: int) -> str:
    for threshold, label in config.RISK_LEVELS:
        if score >= threshold:
            return label
    return "NORMAL"


SEVERITY_ORDER = {"NORMAL": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def severity_at_least(level: str, minimum: str) -> bool:
    return SEVERITY_ORDER.get(level, 0) >= SEVERITY_ORDER.get(minimum, 0)


def is_in_restricted_zone(bbox: tuple, frame_w: int, frame_h: int) -> bool:
    """Legacy v0 helper: bbox overlaps config.RESTRICTED_ZONE."""
    zx1 = int(config.RESTRICTED_ZONE[0] * frame_w)
    zy1 = int(config.RESTRICTED_ZONE[1] * frame_h)
    zx2 = int(config.RESTRICTED_ZONE[2] * frame_w)
    zy2 = int(config.RESTRICTED_ZONE[3] * frame_h)
    x1, y1, x2, y2 = bbox
    return not (x2 < zx1 or x1 > zx2 or y2 < zy1 or y1 > zy2)


def bbox_center_in_zone(bbox: tuple, frame_w: int, frame_h: int) -> bool:
    """Legacy v0 helper: bbox centre inside config.RESTRICTED_ZONE."""
    zx1 = int(config.RESTRICTED_ZONE[0] * frame_w)
    zy1 = int(config.RESTRICTED_ZONE[1] * frame_h)
    zx2 = int(config.RESTRICTED_ZONE[2] * frame_w)
    zy2 = int(config.RESTRICTED_ZONE[3] * frame_h)
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return zx1 <= cx <= zx2 and zy1 <= cy <= zy2


def _center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _dist(a, b):
    (ax, ay), (bx, by) = _center(a), _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _overlaps_head(obj_bbox, person_bbox) -> bool:
    """Object box intersects the top third of the person box (helmet worn)."""
    px1, py1, px2, py2 = person_bbox
    head = (px1, py1, px2, py1 + (py2 - py1) / 3)
    ox1, oy1, ox2, oy2 = obj_bbox
    return not (ox2 < head[0] or ox1 > head[2] or oy2 < head[1] or oy1 > head[3])


class ThreatEngine:
    def __init__(self, zones: ZoneConfig | None = None, weights: dict | None = None):
        self.zones = zones or ZoneConfig()
        self.weights = {
            "multiple_persons": config.SCORE_MULTIPLE_PERSONS,
            "close_proximity":  config.SCORE_CLOSE_PROXIMITY,
            "loitering":        config.SCORE_LOITERING,
            "vandalism":        config.SCORE_VANDALISM_BEHAVIOR,
            "rapid_motion":     config.SCORE_RAPID_MOTION,
            "weapon":           config.SCORE_WEAPON_OBJECT,
            "firearm":          config.SCORE_FIREARM,
            "blunt_tool":       config.SCORE_BLUNT_TOOL,
            "face_concealment": config.SCORE_FACE_CONCEALMENT,
            "threat_object":    config.SCORE_THREAT_OBJECT,
            "unattended_bag":   config.SCORE_UNATTENDED_BAG,
            "restricted_zone":  config.SCORE_RESTRICTED_ZONE,
            "camera_tamper":    config.SCORE_CAMERA_TAMPER,
            "learned_anomaly":  config.SCORE_LEARNED_ANOMALY,
            **(weights or {}),
        }
        self.reset()

    def reset(self):
        self._smoothed_score = 0.0
        self._last_threat_time = None
        self._last_eval = None
        self._object_hits = defaultdict(deque)   # rule -> timestamps of positive frames
        self._bag_still_since = {}                # rounded bag position -> first time seen unattended
        self._tamper_since = None

    # ── temporal helpers ──────────────────────────────────────────────────
    def _persistent(self, rule: str, present: bool, now: float) -> bool:
        hits = self._object_hits[rule]
        if present:
            hits.append(now)
        while hits and now - hits[0] > config.OBJECT_PERSISTENCE_WINDOW_SECONDS:
            hits.popleft()
        return len(hits) >= config.OBJECT_PERSISTENCE_MIN_HITS

    def _unattended_bags(self, bags, persons, now, frame_w, frame_h):
        active = {}
        for bag in bags:
            nearest = min(
                (_dist(bag["bbox"], p["bbox"]) / max(p["bbox"][3] - p["bbox"][1], 1) for p in persons),
                default=float("inf"))
            if nearest <= config.UNATTENDED_BAG_DISTANCE:
                continue
            cx, cy = _center(bag["bbox"])
            key = (round(cx / frame_w * 20), round(cy / frame_h * 20))   # 5% grid cell
            active[key] = self._bag_still_since.get(key, now)
        self._bag_still_since = active
        return [k for k, t0 in active.items() if now - t0 >= config.UNATTENDED_BAG_SECONDS]

    # ── main entry point ──────────────────────────────────────────────────
    def evaluate(self, tracked_persons: list[dict], all_detections: list[dict],
                 frame_w: int, frame_h: int, now: float | None = None,
                 cues: dict | None = None, anomaly: tuple[float, float] | None = None) -> dict:
        """cues: MotionMeter output (enables camera-tamper rule).
        anomaly: (probability, threshold) from the learned temporal head."""
        now = time.time() if now is None else now
        dt = 0.0 if self._last_eval is None else max(0.0, min(now - self._last_eval, 2.0))
        self._last_eval = now

        def conf_ok(d):
            return d["confidence"] >= config.CLASS_CONFIDENCE.get(d["class_name"], 0.0)

        dets = [d for d in all_detections if conf_ok(d)]
        persons = [p for p in tracked_persons if conf_ok(p)]
        in_zone = [p for p in persons if p.get("in_zone", True)]
        signals = []

        def add(rule, message, **evidence):
            signals.append({"rule": rule, "weight": self.weights[rule],
                            "message": message, "evidence": evidence})

        # ── Person / behaviour rules ──────────────────────────────────────
        if len(in_zone) >= 2:
            add("multiple_persons", f"Multiple Persons Detected ({len(in_zone)})",
                count=len(in_zone), track_ids=[p["track_id"] for p in in_zone])
            close = [
                (a["track_id"], b["track_id"])
                for i, a in enumerate(in_zone) for b in in_zone[i + 1:]
                if _dist(a["bbox"], b["bbox"]) /
                   max(((a["bbox"][3] - a["bbox"][1]) + (b["bbox"][3] - b["bbox"][1])) / 2, 1)
                   < config.CLOSE_PROXIMITY_DISTANCE
            ]
            if close:
                add("close_proximity", "Person Within Arm's Reach of ATM User", pairs=close)

        loiterers = [p for p in persons if p.get("loitering")]
        if loiterers:
            dwell = max(p.get("dwell_sec", p.get("duration_sec", 0)) for p in loiterers)
            add("loitering", f"Loitering Detected ({int(dwell)}s)",
                track_ids=[p["track_id"] for p in loiterers], dwell_sec=dwell)

        vandals = [p for p in persons if p.get("vandalism")]
        if vandals:
            add("vandalism", f"Suspicious ATM Tampering ({int(max(p['vandalism_duration'] for p in vandals))}s)",
                track_ids=[p["track_id"] for p in vandals])

        fast = [p for p in persons if p.get("rapid_motion")]
        if fast:
            add("rapid_motion", "Rapid / Erratic Movement in ATM Zone",
                track_ids=[p["track_id"] for p in fast], speed=max(p["speed"] for p in fast))

        restricted = [p for p in persons if p.get("in_restricted")]
        if restricted:
            add("restricted_zone", "Restricted Zone Violation",
                track_ids=[p["track_id"] for p in restricted])

        # ── Object rules (temporal persistence required) ──────────────────
        def objects(classes, zone_only=False):
            out = [d for d in dets if d["class_name"] in classes]
            if zone_only:
                out = [d for d in out if self.zones.contains_center(d["bbox"], frame_w, frame_h)]
            return out

        bladed = objects(BLADED_CLASSES)
        if self._persistent("weapon", bool(bladed), now):
            names = sorted({d["class_name"] for d in bladed}) or ["knife"]
            add("weapon", f"Weapon Detected: {', '.join(names)}",
                max_confidence=max((d["confidence"] for d in bladed), default=None))

        guns = objects(FIREARM_CLASSES)
        if self._persistent("firearm", bool(guns), now):
            add("firearm", "Firearm Detected",
                max_confidence=max((d["confidence"] for d in guns), default=None))

        blunt = objects(BLUNT_CLASSES, zone_only=True)
        if self._persistent("blunt_tool", bool(blunt), now):
            add("blunt_tool", "Blunt Tool Near ATM",
                max_confidence=max((d["confidence"] for d in blunt), default=None))

        helmets = [h for h in objects(HELMET_CLASSES)
                   if any(_overlaps_head(h["bbox"], p["bbox"]) for p in in_zone)]
        if self._persistent("face_concealment", bool(helmets), now):
            add("face_concealment", "Face Concealment (Helmet) in ATM Zone")

        bottles = objects(OBJECT_CLASSES, zone_only=True)
        if self._persistent("threat_object", bool(bottles), now):
            add("threat_object", "Suspicious Object Near ATM")

        if self._unattended_bags(objects(BAG_CLASSES), persons, now, frame_w, frame_h):
            add("unattended_bag", "Unattended Bag Detected")

        # ── Camera tampering (OpenCV image cues, sustained) ───────────────
        if cues is not None:
            tampered = (cues["sharpness_drop"] >= config.CAMERA_TAMPER_SHARPNESS_DROP
                        or cues["brightness"] <= config.CAMERA_TAMPER_DARK)
            self._tamper_since = (self._tamper_since or now) if tampered else None
            if self._tamper_since is not None and now - self._tamper_since >= config.CAMERA_TAMPER_SECONDS:
                add("camera_tamper", "Camera View Obstructed / Tampered",
                    brightness=round(cues["brightness"], 3), sharpness_drop=round(cues["sharpness_drop"], 2))

        # ── Learned temporal anomaly head ─────────────────────────────────
        if anomaly is not None and anomaly[0] >= anomaly[1]:
            add("learned_anomaly", f"Anomalous Behaviour Pattern (p={anomaly[0]:.2f})", probability=round(anomaly[0], 3))

        # ── Score stability (time-based) ──────────────────────────────────
        raw_score = min(sum(s["weight"] for s in signals), 100)
        if raw_score > 0:
            self._last_threat_time = now
            if raw_score > self._smoothed_score:
                self._smoothed_score = min(raw_score, self._smoothed_score + config.SCORE_RISE_PER_SECOND * max(dt, 0.2))
            else:
                # hold while something is still detected; only slow drift down
                self._smoothed_score = max(raw_score, self._smoothed_score - 5.0 * dt)
        elif self._last_threat_time is not None:
            if now - self._last_threat_time >= config.SCORE_HOLD_SECONDS:
                self._smoothed_score = max(0.0, self._smoothed_score - config.SCORE_DECAY_PER_SECOND * dt)
        else:
            self._smoothed_score = 0.0

        final_score = int(round(self._smoothed_score))
        rules = {s["rule"] for s in signals}
        return {
            "score":     final_score,
            "raw_score": raw_score,
            "level":     classify_risk(final_score),
            "reasons":   [s["message"] for s in signals],
            "signals":   signals,
            "details":   {r: (r in rules) for r in self.weights},
            "engine":    ENGINE_VERSION,
        }
