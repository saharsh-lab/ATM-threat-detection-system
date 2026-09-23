# core/tracker.py
# Assigns persistent IDs to detected persons across frames.
# Tracks duration (for loitering) and zone behavior (for vandalism).

import time
import config


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
    def __init__(self):
        self._next_id = 1
        self._tracks  = {}
        # {
        #   id: {
        #     bbox, first_seen, last_seen,
        #     zone_entry_time,   # when person entered restricted zone
        #     vandalism_duration # cumulative seconds exhibiting vandal behavior
        #   }
        # }

    def update(self, person_detections: list[dict],
               frame_w: int, frame_h: int) -> list[dict]:
        """
        Match detections to existing tracks using IoU + center distance fallback.
        Returns detections enriched with tracking info.
        """
        now = time.time()
        matched_ids = set()
        result = []

        # Precompute restricted zone in pixels
        zx1 = int(config.RESTRICTED_ZONE[0] * frame_w)
        zy1 = int(config.RESTRICTED_ZONE[1] * frame_h)
        zx2 = int(config.RESTRICTED_ZONE[2] * frame_w)
        zy2 = int(config.RESTRICTED_ZONE[3] * frame_h)

        for det in person_detections:
            bbox     = det["bbox"]
            best_id  = None
            best_iou = 0.0
            best_dist = float("inf")

            # ── Match by IoU first ────────────────────────────────────────
            for track_id, track in self._tracks.items():
                if track_id in matched_ids:
                    continue
                iou = compute_iou(bbox, track["bbox"])
                if iou > best_iou:
                    best_iou = iou
                    best_id  = track_id

            # ── Fallback: match by center distance if IoU too low ─────────
            if best_iou < config.IOU_MATCH_THRESHOLD:
                for track_id, track in self._tracks.items():
                    if track_id in matched_ids:
                        continue
                    dist = compute_center_distance(bbox, track["bbox"])
                    # Allow match within 120px center movement between frames
                    if dist < best_dist and dist < 120:
                        best_dist = dist
                        best_id   = track_id
                # Only accept distance match if IoU match was poor
                if best_dist < 120 and best_iou < config.IOU_MATCH_THRESHOLD:
                    pass  # best_id already set from distance loop
                elif best_iou < config.IOU_MATCH_THRESHOLD:
                    best_id = None  # truly new person

            if best_id is not None and best_id not in matched_ids:
                # Update existing track
                self._tracks[best_id]["bbox"]      = bbox
                self._tracks[best_id]["last_seen"] = now
                track_id = best_id
            else:
                # New person
                track_id = self._next_id
                self._next_id += 1
                self._tracks[track_id] = {
                    "bbox":               bbox,
                    "first_seen":         now,
                    "last_seen":          now,
                    "zone_entry_time":    None,
                    "vandalism_duration": 0.0,
                }

            matched_ids.add(track_id)
            track = self._tracks[track_id]

            # ── Duration & loitering ──────────────────────────────────────
            duration  = now - track["first_seen"]
            loitering = duration >= config.LOITERING_THRESHOLD_SECONDS

            # ── Vandalism behavior detection ──────────────────────────────
            x1, y1, x2, y2 = bbox
            bw = x2 - x1
            bh = y2 - y1
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            # Is center of person inside restricted zone?
            in_zone = (zx1 <= cx <= zx2) and (zy1 <= cy <= zy2)

            # Is person crouching / hunching? (bbox wider relative to height)
            aspect_ratio = bw / bh if bh > 0 else 0
            hunching = aspect_ratio >= config.VANDALISM_ASPECT_RATIO

            # Track how long person has been in zone with vandal posture
            if in_zone and hunching:
                if track["zone_entry_time"] is None:
                    track["zone_entry_time"] = now
                track["vandalism_duration"] = now - track["zone_entry_time"]
            else:
                # Reset zone timer if they leave or stand upright
                track["zone_entry_time"]    = None
                track["vandalism_duration"] = 0.0

            vandalism = track["vandalism_duration"] >= config.VANDALISM_DURATION_SECONDS

            result.append({
                **det,
                "track_id":           track_id,
                "duration_sec":       round(duration, 1),
                "loitering":          loitering,
                "in_zone":            in_zone,
                "hunching":           hunching,
                "vandalism_duration": round(track["vandalism_duration"], 1),
                "vandalism":          vandalism,
            })

        # ── Remove stale tracks ───────────────────────────────────────────
        stale = [tid for tid, t in self._tracks.items()
                 if now - t["last_seen"] > config.TRACK_EXPIRY_SECONDS]
        for tid in stale:
            del self._tracks[tid]

        return result

    def reset(self):
        self._tracks  = {}
        self._next_id = 1