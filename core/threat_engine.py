# core/threat_engine.py
# Rule-based threat scoring engine.
# Score rises quickly when threats appear.
# Score holds for SCORE_HOLD_SECONDS after threat clears, then decays slowly.
# This prevents flickering while still being responsive.

import time
import config


def classify_risk(score: int) -> str:
    for threshold, label in config.RISK_LEVELS:
        if score >= threshold:
            return label
    return "NORMAL"


def is_in_restricted_zone(bbox: tuple, frame_w: int, frame_h: int) -> bool:
    zx1 = int(config.RESTRICTED_ZONE[0] * frame_w)
    zy1 = int(config.RESTRICTED_ZONE[1] * frame_h)
    zx2 = int(config.RESTRICTED_ZONE[2] * frame_w)
    zy2 = int(config.RESTRICTED_ZONE[3] * frame_h)
    x1, y1, x2, y2 = bbox
    return not (x2 < zx1 or x1 > zx2 or y2 < zy1 or y1 > zy2)


def bbox_center_in_zone(bbox: tuple, frame_w: int, frame_h: int) -> bool:
    """Stricter check — only flags if CENTER of bbox is inside zone."""
    zx1 = int(config.RESTRICTED_ZONE[0] * frame_w)
    zy1 = int(config.RESTRICTED_ZONE[1] * frame_h)
    zx2 = int(config.RESTRICTED_ZONE[2] * frame_w)
    zy2 = int(config.RESTRICTED_ZONE[3] * frame_h)
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return zx1 <= cx <= zx2 and zy1 <= cy <= zy2


class ThreatEngine:
    def __init__(self):
        self._smoothed_score  = 0.0
        self._last_threat_time = None   # when did we last see a raw threat?

    def evaluate(self,
                 tracked_persons: list[dict],
                 all_detections:  list[dict],
                 frame_w: int,
                 frame_h: int) -> dict:

        now       = time.time()
        raw_score = 0
        reasons   = []
        details   = {
            "multiple_persons": False,
            "loitering":        False,
            "unattended_bag":   False,
            "restricted_zone":  False,
            "vandalism":        False,
            "weapon":           False,
            "threat_object":    False,
        }

        bag_classes    = {"backpack", "handbag", "suitcase"}
        weapon_classes = {"knife", "scissors"}
        object_classes = {"bottle"}

        bags    = [d for d in all_detections if d["class_name"] in bag_classes]
        weapons = [d for d in all_detections if d["class_name"] in weapon_classes]
        objects = [d for d in all_detections if d["class_name"] in object_classes]

        person_count = len(tracked_persons)

        # ── Rule 1: Multiple Persons ──────────────────────────────────────
        if person_count >= 2:
            raw_score += config.SCORE_MULTIPLE_PERSONS
            reasons.append(f"Multiple Persons Detected ({person_count})")
            details["multiple_persons"] = True

        # ── Rule 2: Loitering ─────────────────────────────────────────────
        loiterers = [p for p in tracked_persons if p.get("loitering", False)]
        if loiterers:
            raw_score += config.SCORE_LOITERING
            max_dur = max(p["duration_sec"] for p in loiterers)
            reasons.append(f"Loitering Detected ({int(max_dur)}s)")
            details["loitering"] = True

        # ── Rule 3: Unattended Bag ────────────────────────────────────────
        for bag in bags:
            bx1, by1, bx2, by2 = bag["bbox"]
            bag_cx = (bx1 + bx2) / 2
            bag_cy = (by1 + by2) / 2
            attended = any(
                ((bag_cx - (p["bbox"][0] + p["bbox"][2]) / 2) ** 2 +
                 (bag_cy - (p["bbox"][1] + p["bbox"][3]) / 2) ** 2) ** 0.5 < 180
                for p in tracked_persons
            )
            if not attended:
                raw_score += config.SCORE_UNATTENDED_BAG
                reasons.append("Unattended Bag Detected")
                details["unattended_bag"] = True
                break

        # ── Rule 4: Restricted Zone Violation ─────────────────────────────
        for person in tracked_persons:
            if is_in_restricted_zone(person["bbox"], frame_w, frame_h):
                raw_score += config.SCORE_RESTRICTED_ZONE
                reasons.append("Restricted Zone Violation")
                details["restricted_zone"] = True
                break

        # ── Rule 5: Vandalism / Tampering Behavior ────────────────────────
        vandals = [p for p in tracked_persons if p.get("vandalism", False)]
        if vandals:
            raw_score += config.SCORE_VANDALISM_BEHAVIOR
            max_vdur = max(p["vandalism_duration"] for p in vandals)
            reasons.append(f"Suspicious ATM Tampering ({int(max_vdur)}s)")
            details["vandalism"] = True

        # ── Rule 6: Weapon Detected (knife, scissors) ─────────────────────
        if weapons:
            raw_score += config.SCORE_WEAPON_OBJECT
            names = list({w["class_name"] for w in weapons})
            reasons.append(f"Weapon Detected: {', '.join(names)}")
            details["weapon"] = True

        # ── Rule 7: Threat Object Near ATM (bottle, canister) ────────────
        # Only flag if the object is in the ATM zone — not every bottle counts
        threat_objects_in_zone = [
            o for o in objects
            if bbox_center_in_zone(o["bbox"], frame_w, frame_h)
        ]
        if threat_objects_in_zone:
            raw_score += config.SCORE_THREAT_OBJECT
            reasons.append("Suspicious Object Near ATM")
            details["threat_object"] = True

        # ── Score Stability Logic ─────────────────────────────────────────
        raw_score = min(raw_score, 100)

        if raw_score > 0:
            # Threats are active — record time and rise toward raw score
            self._last_threat_time = now
            diff = raw_score - self._smoothed_score
            if diff > 0:
                # Rise fast
                self._smoothed_score = min(
                    raw_score,
                    self._smoothed_score + config.SCORE_RISE_STEP
                )
            else:
                # Even if raw is slightly lower, hold score while threats active
                # Only allow tiny drop while something is still detected
                self._smoothed_score = max(
                    raw_score,
                    self._smoothed_score - 1
                )
        else:
            # No threats detected this frame
            if self._last_threat_time is not None:
                elapsed = now - self._last_threat_time
                if elapsed < config.SCORE_HOLD_SECONDS:
                    # Hold — don't decay yet, threat just cleared
                    pass
                else:
                    # Decay slowly
                    self._smoothed_score = max(
                        0.0,
                        self._smoothed_score - config.SCORE_DECAY_STEP
                    )
            else:
                self._smoothed_score = 0.0

        final_score = int(round(self._smoothed_score))
        level       = classify_risk(final_score)

        return {
            "score":   final_score,
            "level":   level,
            "reasons": reasons,
            "details": details,
        }

    def reset(self):
        self._smoothed_score   = 0.0
        self._last_threat_time = None