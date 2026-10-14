# core/camera.py
# One worker thread per camera: capture → detect/track → behaviour → threat →
# incidents/alerts → annotated JPEG shared by every MJPEG client.
#
# Source kinds (config/cameras.yaml):
#   file    a video in VIDEO_DIR or the upload dir, replayed in real time and
#           labelled RECORDED everywhere (never presented as live)
#   rtsp/http  network camera; the URL is read from an environment variable
#           (`url_env`) so credentials never live in config files or the DB
#   webcam  local device index
# Network/webcam sources reconnect with exponential backoff; inference errors
# are counted and surfaced in the camera status rather than crashing.

import logging
import os
import threading
import time
from collections import deque

import cv2
import yaml

import config
from core.detector import Detector
from core.incidents import IncidentManager
from core.temporal import MotionMeter, TemporalFeatures, TemporalHead
from core.threat_engine import ENGINE_VERSION, ThreatEngine
from core.tracker import PersonTracker
from core.zones import ZoneConfig
from data.annotations import is_anomalous_frame
from core.drawing import (draw_hud, draw_objects, draw_tracker_info, draw_zones,
                           encode_frame_to_jpeg, get_risk_color_hex)

log = logging.getLogger("sentinelx.camera")

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v")
UPLOAD_DIR = os.path.join(config.INSTANCE_DIR, "uploads")


def video_roots() -> list[str]:
    return [config.VIDEO_DIR, UPLOAD_DIR]


def list_videos() -> list[dict]:
    out = []
    for root, origin in ((config.VIDEO_DIR, "dataset"), (UPLOAD_DIR, "upload")):
        if os.path.isdir(root):
            for f in sorted(os.listdir(root), key=lambda n: (len(n), n)):
                if f.lower().endswith(VIDEO_EXTENSIONS):
                    out.append({"name": f, "origin": origin})
    return out


def resolve_video(name: str) -> str:
    """Map a bare file name to a path inside an allowed video root."""
    if not name or os.path.basename(name) != name or name.startswith("."):
        raise ValueError("invalid video name")
    if not name.lower().endswith(VIDEO_EXTENSIONS):
        raise ValueError("unsupported video type")
    for root in video_roots():
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(name)


def probe_video_file(path: str) -> bool:
    cap = cv2.VideoCapture(path)
    try:
        return cap.isOpened() and cap.read()[0]
    finally:
        cap.release()


def load_camera_configs(path: str | None = None) -> list[dict]:
    path = path or (config.CAMERAS_FILE if os.path.exists(config.CAMERAS_FILE) else config.CAMERAS_EXAMPLE_FILE)
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    cams = []
    seen = set()
    for c in data.get("cameras", []):
        cid = str(c["id"])
        if cid in seen:
            raise ValueError(f"duplicate camera id {cid}")
        seen.add(cid)
        src = c.get("source", {})
        stype = src.get("type", "file")
        if stype not in ("file", "rtsp", "http", "webcam"):
            raise ValueError(f"camera {cid}: unknown source type {stype}")
        cams.append({
            "id": cid, "name": c.get("name", cid), "location": c.get("location"),
            "enabled": bool(c.get("enabled", True)), "source_type": stype,
            "source": src, "zones": ZoneConfig.from_dict(c.get("zones")),
            "video_ref": src.get("file") if stype == "file" else None,
        })
    return cams


class CameraWorker(threading.Thread):
    def __init__(self, cam: dict, settings, alerts, model_id: str = config.MODEL_ID, db=None):
        super().__init__(name=f"camera-{cam['id']}", daemon=True)
        self.cam = cam
        self.settings = settings
        self.alerts = alerts
        self.model_id = model_id
        self._db = db
        self._stop = threading.Event()
        self._switch = None
        self._lock = threading.Lock()
        self._frame_cond = threading.Condition()
        self._jpeg = None
        self._jpeg_seq = 0
        self.detector = None
        self.tracker = PersonTracker(cam["zones"])
        self.engine = ThreatEngine(cam["zones"])
        self.motion = MotionMeter(cam["zones"])
        self.temporal = TemporalFeatures()
        self.head = None
        self.incidents = None
        self.preroll = deque(maxlen=int(30 * config.INFER_FPS))
        self.state = {
            "camera_id": cam["id"], "name": cam["name"], "location": cam.get("location"),
            "source_type": cam["source_type"],
            "source_kind": "recorded" if cam["source_type"] == "file" else "live",
            "video": cam.get("video_ref"), "status": "starting", "error": None,
            "frame": 0, "media_time": 0.0, "fps": 0.0, "infer_fps": 0.0, "latency_ms": 0.0,
            "threat": {"score": 0, "level": "NORMAL", "reasons": [], "signals": []},
            "tracks": [], "objects": [], "ground_truth": None, "incident_id": None,
            "updated_at": None, "inference_errors": 0, "frame_size": None,
        }

    # ── public API ───────────────────────────────────────────────────────
    def snapshot_state(self) -> dict:
        with self._lock:
            s = dict(self.state)
        s["threat"] = {**s["threat"], "color": get_risk_color_hex(s["threat"]["level"])}
        return s

    def latest_jpeg(self, after_seq: int = -1, timeout: float = 2.0):
        with self._frame_cond:
            if self._jpeg_seq <= after_seq:
                self._frame_cond.wait(timeout)
            return self._jpeg_seq, self._jpeg

    def switch_video(self, name: str):
        if self.cam["source_type"] != "file":
            raise ValueError("only recorded-file cameras can switch video")
        path = resolve_video(name)
        if not probe_video_file(path):
            raise ValueError("video file cannot be decoded")
        self._switch = name

    def stop(self):
        self._stop.set()

    # ── internals ────────────────────────────────────────────────────────
    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _open(self):
        src = self.cam["source"]
        stype = self.cam["source_type"]
        if stype == "file":
            path = resolve_video(self.cam["video_ref"])
            return cv2.VideoCapture(path)
        if stype == "webcam":
            return cv2.VideoCapture(int(src.get("index", 0)))
        url = os.environ.get(src.get("url_env", ""), "") or src.get("url", "")
        if not url:
            raise ValueError(f"no stream URL: set environment variable {src.get('url_env')!r}")
        return cv2.VideoCapture(url, cv2.CAP_FFMPEG)

    def _reset_pipeline(self):
        self.tracker.reset()
        self.engine.reset()
        self.motion.reset()
        self.temporal.reset()
        if self.detector:
            self.detector.reset_tracking()
        self.preroll.clear()

    def run(self):
        try:
            self.detector = Detector(self.model_id)
        except Exception as e:   # noqa: BLE001
            log.exception("camera %s: model failed to load", self.cam["id"])
            self._set(status="error", error=f"model failed to load: {e}")
            return
        info = self.detector.info()
        info["dataset_id"] = self.detector.spec.get("dataset_id")
        if config.TEMPORAL_HEAD_ENABLED:
            try:
                self.head = TemporalHead.for_model(self.detector.version)
            except (ValueError, KeyError) as e:
                log.warning("temporal head for %s unusable: %s", self.detector.version, e)
        info["temporal_head"] = self.head.version if self.head else None
        try:   # incidents reference model_versions(id): register the model actually loaded
            from core import repository
            repository.upsert_model({"id": info["id"], "name": info["name"], "weights_path": self.detector.spec["weights"],
                                     "weights_sha256": self.detector.spec.get("sha256"),
                                     "class_schema": info["schema"], "dataset_id": None,
                                     "engine_version": ENGINE_VERSION}, db=self._db)
        except Exception:   # noqa: BLE001
            log.exception("could not register model %s", info["id"])
        self.incidents = IncidentManager(self.cam, self.settings, self.alerts, info, ENGINE_VERSION, db=self._db)
        self._set(model=info)

        backoff = 1.0
        while not self._stop.is_set():
            try:
                cap = self._open()
            except (ValueError, FileNotFoundError) as e:
                self._set(status="error", error=str(e))
                self._stop.wait(5.0)
                continue
            if not cap.isOpened():
                self._set(status="offline", error="camera unavailable — retrying")
                cap.release()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            self._set(status="live" if self.cam["source_type"] != "file" else "replaying", error=None)
            self._end_reason = "stream interrupted"
            try:
                self._loop(cap)
            except Exception as e:   # noqa: BLE001
                log.exception("camera %s pipeline error", self.cam["id"])
                self._set(status="error", error=f"pipeline error: {e}")
                self._stop.wait(2.0)
            finally:
                cap.release()
                self.incidents.close(self._end_reason)
                self._reset_pipeline()
        self._set(status="stopped")

    def _loop(self, cap):
        is_file = self.cam["source_type"] == "file"
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps <= 1 or fps > 120:
            fps = 25.0
        infer_every = 1.0 / config.INFER_FPS
        frame_idx = 0
        t_start = time.monotonic()
        last_infer_t = -1e9
        tracked, objects, threat = [], [], self.state["threat"]
        fps_win, infer_win = deque(maxlen=30), deque(maxlen=30)
        video_name = self.cam.get("video_ref")
        failures = 0

        while not self._stop.is_set():
            if self._switch:
                name, self._switch = self._switch, None
                self.incidents.close("operator switched video")
                self.cam["video_ref"] = name
                self._set(video=name)
                return   # reopen with the new file

            ok, frame = cap.read()
            if not ok:
                if is_file:
                    # Recorded file ended: return so run() reopens it from the start
                    # (seeking back after EOF is unreliable across OpenCV backends);
                    # run() closes the incident and resets tracking state.
                    if frame_idx == 0:
                        raise RuntimeError("video file produced no frames")
                    self._end_reason = "recording ended"
                    return
                failures += 1
                if failures > 25:
                    self._set(status="offline", error="stream lost — reconnecting")
                    return
                time.sleep(0.04)
                continue
            failures = 0
            frame_idx += 1
            now_wall = time.monotonic()
            media_t = frame_idx / fps if is_file else now_wall
            h, w = frame.shape[:2]

            if (media_t - last_infer_t) >= infer_every:
                last_infer_t = media_t
                dets = self.detector.detect(frame)
                infer_win.append(now_wall)
                persons = [d for d in dets if d["class_name"] == "person"]
                objects = [d for d in dets if d["class_name"] != "person"]
                tracked = self.tracker.update(persons, w, h, now=media_t)
                cues = self.motion.update(frame, media_t)
                anomaly = None
                if self.head is not None:
                    x = self.temporal.update(media_t, tracked, dets, cues)
                    anomaly = (self.head.predict(x), self.head.threshold)
                threat = self.engine.evaluate(tracked, dets, w, h, now=media_t, cues=cues, anomaly=anomaly)

                annotated = self._annotate(frame.copy(), tracked, objects, threat, is_file, video_name)
                small = annotated if w <= 960 else cv2.resize(annotated, (960, int(h * 960 / w)))
                self.preroll.append(small)
                jpeg = encode_frame_to_jpeg(annotated)
                self.incidents.update(threat, dets, frame_idx, media_t if is_file else None,
                                      annotated_jpeg=jpeg, preroll=list(self.preroll), fps=config.INFER_FPS)
                gt = is_anomalous_frame(video_name, frame_idx) if is_file and video_name else None
                self._set(threat=threat, tracks=_public_tracks(tracked), objects=_public_objects(objects),
                          frame=frame_idx, media_time=round(media_t, 2) if is_file else None,
                          latency_ms=round(self.detector.last_latency_ms, 1), ground_truth=gt,
                          incident_id=self.incidents.active_id, inference_errors=self.detector.errors,
                          frame_size=[w, h], updated_at=time.time(),
                          infer_fps=round(_rate(infer_win), 1))
            else:
                annotated = self._annotate(frame.copy(), tracked, objects, threat, is_file, video_name)
                jpeg = encode_frame_to_jpeg(annotated)

            with self._frame_cond:
                self._jpeg = jpeg
                self._jpeg_seq += 1
                self._frame_cond.notify_all()
            fps_win.append(now_wall)
            self._set(fps=round(_rate(fps_win), 1))

            if is_file:
                # real-time replay pacing
                delay = t_start + frame_idx / fps - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

    def _annotate(self, frame, tracked, objects, threat, is_file, video_name):
        draw_zones(frame, self.cam["zones"], threat["level"])
        draw_objects(frame, objects)
        draw_tracker_info(frame, tracked)
        label = f"RECORDED  {video_name}" if is_file else f"LIVE  {self.cam['name']}"
        draw_hud(frame, threat, label)
        return frame


def _rate(win) -> float:
    return (len(win) - 1) / (win[-1] - win[0]) if len(win) > 1 and win[-1] > win[0] else 0.0


def _public_tracks(tracked):
    keys = ("track_id", "bbox", "confidence", "dwell_sec", "loitering", "vandalism", "rapid_motion",
            "in_zone", "speed")
    return [{k: t.get(k) for k in keys} for t in tracked]


def _public_objects(objects):
    return [{"class_name": o["class_name"], "confidence": o["confidence"], "bbox": o["bbox"],
             "track_id": o.get("track_id")} for o in objects]


class CameraManager:
    def __init__(self, settings, alerts, db=None, cameras: list[dict] | None = None):
        self.settings = settings
        self.alerts = alerts
        self._db = db
        self.cameras = cameras if cameras is not None else load_camera_configs()
        self.workers: dict[str, CameraWorker] = {}

    def start(self):
        from core import repository
        for cam in self.cameras:
            try:
                repository.upsert_camera({
                    "id": cam["id"], "name": cam["name"], "location": cam.get("location"),
                    "source_type": cam["source_type"],
                    "source_ref": cam.get("video_ref") or cam["source"].get("url_env") or
                                  str(cam["source"].get("index", "")),
                    "zone_config": cam["zones"].to_dict(), "enabled": cam["enabled"]}, db=self._db)
            except Exception:   # noqa: BLE001
                log.exception("could not register camera %s", cam["id"])
            if cam["enabled"]:
                w = CameraWorker(cam, self.settings, self.alerts, db=self._db)
                self.workers[cam["id"]] = w
                w.start()

    def get(self, camera_id: str) -> CameraWorker | None:
        return self.workers.get(camera_id)

    def states(self) -> list[dict]:
        out = []
        for cam in self.cameras:
            w = self.workers.get(cam["id"])
            if w:
                out.append(w.snapshot_state())
            else:
                out.append({"camera_id": cam["id"], "name": cam["name"], "status": "disabled",
                            "source_type": cam["source_type"]})
        return out

    def stop(self):
        for w in self.workers.values():
            w.stop()
