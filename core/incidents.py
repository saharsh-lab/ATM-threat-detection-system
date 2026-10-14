# core/incidents.py
# Turns the per-frame threat stream of one camera into incident records.
#
#   NORMAL ──score ≥ open_score──▶ OPEN ──(score < open_score for close_seconds)──▶ CLOSED
#
# While open: progress is written at most once per second, detections are
# sampled every DETECTION_SAMPLE_SECONDS, a snapshot is stored at open and at
# each severity escalation, and a pre-event clip is written in the background.

import logging
import re
import threading
import time

import cv2

import config
from core import evidence, repository
from core.db import utcnow
from core.threat_engine import SEVERITY_ORDER

log = logging.getLogger("sentinelx.incidents")


class IncidentManager:
    def __init__(self, camera: dict, settings, alerts, model_info: dict, engine_version: str,
                 db=None, clock=time.monotonic):
        self.camera = camera
        self.settings = settings
        self.alerts = alerts
        self.model_info = model_info
        self.engine_version = engine_version
        self._db = db
        self._clock = clock
        self.active_id = None
        self._reset_state()

    def _reset_state(self):
        self._below_since = None
        self._last_write = 0.0
        self._last_det_sample = 0.0
        self._severity = None
        self._peak = 0
        self._reasons, self._rules, self._classes = [], set(), set()
        self._max_conf = None

    @property
    def source_kind(self) -> str:
        return "recorded" if self.camera["source_type"] == "file" else "live"

    def _accumulate(self, threat, detections):
        for r in threat["reasons"]:
            # one entry per signal: "Loitering Detected (21s)" replaces "(20s)"
            stem = re.sub(r"\s*\(.*\)$", "", r)
            for i, old in enumerate(self._reasons):
                if re.sub(r"\s*\(.*\)$", "", old) == stem:
                    self._reasons[i] = r
                    break
            else:
                self._reasons.append(r)
        self._rules |= {s["rule"] for s in threat.get("signals", [])}
        for d in detections:
            self._classes.add(d["class_name"])
            if d["class_name"] != "person":
                self._max_conf = max(self._max_conf or 0.0, d["confidence"])
        self._peak = max(self._peak, threat["score"])

    def update(self, threat: dict, detections: list[dict], frame_idx: int | None, media_t: float | None,
               annotated_jpeg: bytes | None = None, preroll: list | None = None, fps: float = 10.0):
        """Feed one evaluated frame. Never raises: DB errors are logged so the
        video pipeline keeps running."""
        try:
            self._update(threat, detections, frame_idx, media_t, annotated_jpeg, preroll, fps)
        except Exception:   # noqa: BLE001
            log.exception("incident update failed for camera %s", self.camera["id"])

    def _update(self, threat, detections, frame_idx, media_t, jpeg, preroll, fps):
        now = self._clock()
        score, level = threat["score"], threat["level"]
        open_score = self.settings.get("incident_open_score")

        if self.active_id is None:
            if score < open_score or level == "NORMAL":
                return
            self._reset_state()
            self._accumulate(threat, detections)
            self._severity = level
            self.active_id = repository.create_incident({
                "camera_id": self.camera["id"], "source_kind": self.source_kind,
                "video_ref": self.camera.get("video_ref"), "frame_start": frame_idx, "frame_end": frame_idx,
                "media_time_start": media_t, "media_time_end": media_t, "threat_score": score,
                "severity": level, "primary_reason": (threat["reasons"] or [None])[0],
                "reasons": self._reasons, "rules": sorted(self._rules),
                "detection_classes": sorted(self._classes), "max_confidence": self._max_conf,
                "model_version": self.model_info.get("id"), "engine_version": self.engine_version,
                "dataset_id": self.model_info.get("dataset_id"),
            }, db=self._db)
            log.info("incident %s opened on %s (%s, score %d)", self.active_id, self.camera["id"], level, score)
            if jpeg:
                evidence.save_snapshot(self.camera["id"], self.active_id, jpeg, db=self._db)
            clip_s = self.settings.get("evidence_clip_seconds")
            if preroll and clip_s > 0:
                frames = [f for f in preroll][-int(clip_s * fps):]
                inc = self.active_id
                threading.Thread(target=evidence.save_clip, args=(self.camera["id"], inc, frames, fps, self._db),
                                 daemon=True).start()
            self._persist_detections(detections, frame_idx, score, now)
            self.alerts.dispatch(self.active_id, self.camera["id"], self.camera["name"], level, score,
                                 threat["reasons"], self._rules, self.source_kind)
            self._last_write = now
            return

        # ── active incident ──────────────────────────────────────────────
        self._accumulate(threat, detections)
        escalated = level != "NORMAL" and SEVERITY_ORDER[level] > SEVERITY_ORDER[self._severity]
        if escalated:
            self._severity = level
            if jpeg:
                evidence.save_snapshot(self.camera["id"], self.active_id, jpeg, db=self._db)
        if escalated or threat["reasons"]:
            self.alerts.dispatch(self.active_id, self.camera["id"], self.camera["name"], self._severity,
                                 score, threat["reasons"], self._rules, self.source_kind)
        if now - self._last_det_sample >= config.DETECTION_SAMPLE_SECONDS:
            self._persist_detections(detections, frame_idx, score, now)

        closing = False
        if score < open_score:
            self._below_since = self._below_since or now
            closing = now - self._below_since >= self.settings.get("incident_close_seconds")
        else:
            self._below_since = None

        if closing or escalated or now - self._last_write >= 1.0:
            repository.update_incident_progress(self.active_id, {
                "threat_score": score, "severity": self._severity, "frame_end": frame_idx,
                "media_time_end": media_t, "reasons": self._reasons, "rules": sorted(self._rules),
                "detection_classes": sorted(self._classes), "max_confidence": self._max_conf,
                "ended_at": utcnow() if closing else None,
            }, db=self._db)
            self._last_write = now
        if closing:
            log.info("incident %s closed (peak %d)", self.active_id, self._peak)
            self.active_id = None
            self._reset_state()

    def _persist_detections(self, detections, frame_idx, score, now):
        repository.add_detections(self.active_id, detections[:50], utcnow(), frame_idx, score, db=self._db)
        self._last_det_sample = now

    def close(self, reason: str = "stream stopped"):
        """Force-close the active incident (camera switch / shutdown)."""
        if self.active_id is None:
            return
        try:
            repository.update_incident_progress(self.active_id, {
                "threat_score": 0, "severity": self._severity, "reasons": self._reasons + [f"Closed: {reason}"],
                "rules": sorted(self._rules), "detection_classes": sorted(self._classes),
                "max_confidence": self._max_conf, "ended_at": utcnow()}, db=self._db)
        except Exception:   # noqa: BLE001
            log.exception("could not close incident %s", self.active_id)
        self.active_id = None
        self._reset_state()


def encode_jpeg(frame, quality: int = config.JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""
