# core/tracker.py
# Assigns persistent IDs to detected persons across frames and derives
# behavioural features per track: dwell time in the ATM zone (loitering),
# posture (tampering), and motion (rapid / erratic movement).
#
# If detections already carry a `track_id` (ByteTrack via Ultralytics), that
# ID is used directly; otherwise the original IoU + centre-distance matcher
# assigns one. All timing uses the `now` argument (media time for video files,
# wall-clock for live streams) so results do not depend on processing speed.

import time
from collections import deque

import config
from core.zones import ZoneConfig


def compute_iou(boxA, boxB) -> float:
    """Intersection over Union between two bounding boxes."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    if inter == 0:
        return 0.0
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    return inter / float(areaA + areaB - inter)


def compute_center_distance(boxA, boxB) -> float:
    """Euclidean distance between bbox centers — fallback matcher."""
    cxA = (boxA[0] + boxA[2]) / 2
    cyA = (boxA[1] + boxA[3]) / 2
    cxB = (boxB[0] + boxB[2]) / 2
    cyB = (boxB[1] + boxB[3]) / 2
    return ((cxA - cxB) ** 2 + (cyA - cyB) ** 2) ** 0.5


class PersonTracker:
    def __init__(self, zones: ZoneConfig | None = None):
        self.zones = zones or ZoneConfig()
        self._next_id = 1
        self._tracks = {}
        self._last_now = None

    def _new_track(self, bbox, now):
        return {
            "bbox": bbox, "first_seen": now, "last_seen": now,
            "zone_dwell": 0.0,              # cumulative seconds inside the ATM zone
            "zone_entry_time": None,        # start of current hunched-in-zone streak
            "vandalism_duration": 0.0,
            "history": deque(),             # (t, cx, cy, h)
            "fast_since": None,
        }

    def _match(self, bbox, matched_ids):
        best_id, best_iou = None, 0.0
        for track_id, track in self._tracks.items():
            if track_id in matched_ids:
                continue
            iou = compute_iou(bbox, track["bbox"])
            if iou > best_iou:
                best_iou, best_id = iou, track_id
        if best_iou >= config.IOU_MATCH_THRESHOLD:
            return best_id
        # Fallback: nearest centre within CENTER_MATCH_MAX_DIST
        best_id, best_dist = None, float("inf")
        for track_id, track in self._tracks.items():
            if track_id in matched_ids:
                continue
            dist = compute_center_distance(bbox, track["bbox"])
            if dist < best_dist and dist < config.CENTER_MATCH_MAX_DIST:
                best_dist, best_id = dist, track_id
        return best_id

    def update(self, person_detections: list[dict], frame_w: int, frame_h: int,
               now: float | None = None) -> list[dict]:
        """Returns the detections enriched with tracking and behaviour fields."""
        now = time.time() if now is None else now
        dt = 0.0 if self._last_now is None else max(0.0, min(now - self._last_now, 1.0))
        self._last_now = now
        matched_ids = set()
        result = []

        for det in person_detections:
            bbox = tuple(int(v) for v in det["bbox"])
            ext_id = det.get("track_id")
            if ext_id is not None:
                track_id = int(ext_id)
                if track_id not in self._tracks:
                    self._tracks[track_id] = self._new_track(bbox, now)
            else:
                track_id = self._match(bbox, matched_ids)
                if track_id is None:
                    track_id = self._next_id
                    self._next_id += 1
                    self._tracks[track_id] = self._new_track(bbox, now)
            matched_ids.add(track_id)
            track = self._tracks[track_id]
            track["bbox"] = bbox
            track["last_seen"] = now

            x1, y1, x2, y2 = bbox
            bw, bh = x2 - x1, y2 - y1
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            in_zone = self.zones.in_atm_zone(bbox, frame_w, frame_h)
            in_restricted = self.zones.in_restricted(bbox, frame_w, frame_h)
            if in_zone:
                track["zone_dwell"] += dt

            # ── Duration & loitering ──────────────────────────────────────
            duration = now - track["first_seen"]
            loitering = track["zone_dwell"] >= self.zones.loitering_seconds

            # ── Posture: crouching / hunching at the machine ──────────────
            aspect_ratio = bw / bh if bh > 0 else 0
            hunching = aspect_ratio >= config.VANDALISM_ASPECT_RATIO
            if in_zone and hunching:
                if track["zone_entry_time"] is None:
                    track["zone_entry_time"] = now
                track["vandalism_duration"] = now - track["zone_entry_time"]
            else:
                track["zone_entry_time"] = None
                track["vandalism_duration"] = 0.0
            vandalism = track["vandalism_duration"] >= config.VANDALISM_DURATION_SECONDS

            # ── Motion: speed in bbox-heights per second ──────────────────
            hist = track["history"]
            hist.append((now, cx, cy, max(bh, 1)))
            while hist and now - hist[0][0] > config.TRACK_HISTORY_SECONDS:
                hist.popleft()
            speed = 0.0
            recent = [h for h in hist if now - h[0] <= 1.0]
            if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
                path = sum(((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5
                           for a, b in zip(recent, recent[1:]))
                mean_h = sum(h[3] for h in recent) / len(recent)
                speed = path / mean_h / (recent[-1][0] - recent[0][0])
            if speed >= config.RAPID_MOTION_SPEED and in_zone:
                if track["fast_since"] is None:
                    track["fast_since"] = now
            else:
                track["fast_since"] = None
            rapid_motion = (track["fast_since"] is not None and
                            now - track["fast_since"] >= config.RAPID_MOTION_SECONDS)

            result.append({
                **det,
                "bbox":               bbox,
                "track_id":           track_id,
                "duration_sec":       round(duration, 1),
                "dwell_sec":          round(track["zone_dwell"], 1),
                "loitering":          loitering,
                "in_zone":            in_zone,
                "in_restricted":      in_restricted,
                "hunching":           hunching,
                "vandalism_duration": round(track["vandalism_duration"], 1),
                "vandalism":          vandalism,
                "speed":              round(speed, 2),
                "rapid_motion":       rapid_motion,
            })

        # ── Remove stale tracks ───────────────────────────────────────────
        stale = [tid for tid, t in self._tracks.items()
                 if now - t["last_seen"] > config.TRACK_EXPIRY_SECONDS]
        for tid in stale:
            del self._tracks[tid]

        return result

    def reset(self):
        self._tracks = {}
        self._next_id = 1
        self._last_now = None
