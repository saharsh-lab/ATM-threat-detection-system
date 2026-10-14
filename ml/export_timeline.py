# ml/export_timeline.py
# Precomputes REAL detections, tracking and threat scores (SentinelX v2
# pipeline) for the serverless demo, which cannot run inference itself.
# Every file records which model / engine produced it; the dashboard labels
# this data as "Demo · precomputed" and never as live.
#
#   python -m ml.export_timeline [--model sentinelx-det-v1] [--device cpu]

import argparse
import json
import os
from datetime import datetime, timezone

import cv2

import config
from core.detector import Detector
from core.temporal import MotionMeter, TemporalFeatures, TemporalHead
from core.threat_engine import ENGINE_VERSION, ThreatEngine
from core.tracker import PersonTracker
from data.annotations import is_anomalous_frame

VIDEOS = ["28.mp4", "12.mp4", "30a.mp4", "57.mp4", "3.mp4"]
OUTPUT_FILE = os.path.join(config.BASE_DIR, "static", "data", "threat_timelines.json")
THREAT_CLASSES = {"knife", "scissors", "firearm", "blunt_tool"}


def process_video(video_name, detector, infer_fps, head=None):
    video_path = os.path.join(config.BASE_DIR, "static", "videos", video_name)
    if not os.path.exists(video_path):
        print(f"Video {video_path} not found!")
        return None

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tracker, engine = PersonTracker(), ThreatEngine()
    meter, temporal = MotionMeter(), TemporalFeatures()      # same pipeline as the live camera worker
    detector.reset_tracking()

    frames_data, frame_idx, last_t = [], 0, -1e9
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        t = frame_idx / fps
        if t - last_t < 1.0 / infer_fps:
            continue
        last_t = t
        dets = detector.detect(frame)
        persons = [d for d in dets if d["class_name"] == "person"]
        tracked = tracker.update(persons, w, h, now=t)
        cues = meter.update(frame, t)
        anomaly = None
        if head is not None:
            anomaly = (head.predict(temporal.update(t, tracked, dets, cues)), head.threshold)
        threat = engine.evaluate(tracked, dets, w, h, now=t, cues=cues, anomaly=anomaly)
        boxes = [{"bbox": list(p["bbox"]), "cls": "person", "conf": round(p["confidence"], 3), "tid": p["track_id"],
                  "dwell": p["dwell_sec"], "flag": bool(p["loitering"] or p["vandalism"] or p["rapid_motion"])}
                 for p in tracked]
        boxes += [{"bbox": list(d["bbox"]), "cls": d["class_name"], "conf": round(d["confidence"], 3),
                   "tid": d.get("track_id"), "flag": d["class_name"] in THREAT_CLASSES}
                  for d in dets if d["class_name"] != "person"]
        frames_data.append({
            "t": round(t, 2), "f": frame_idx, "s": threat["score"], "l": threat["level"], "r": threat["reasons"],
            "sig": [{"rule": s["rule"], "weight": s["weight"], "message": s["message"]} for s in threat["signals"]],
            "gt": is_anomalous_frame(video_name, frame_idx), "p": len(tracked),
            "b": sum(1 for d in dets if d["class_name"] in ("backpack", "handbag", "suitcase")), "boxes": boxes,
        })
    cap.release()
    return {"video": video_name, "width": w, "height": h, "fps": fps, "total_frames": frame_idx,
            "duration": round(frame_idx / fps, 2), "timeline": frames_data}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=config.MODEL_ID)
    p.add_argument("--device", default=None)
    p.add_argument("--infer-fps", type=float, default=config.INFER_FPS)
    args = p.parse_args()
    detector = Detector(args.model, tracking=True, device=args.device)
    head = TemporalHead.for_model(detector.version) if config.TEMPORAL_HEAD_ENABLED else None
    meta = {"model": detector.version, "engine": ENGINE_VERSION, "infer_fps": args.infer_fps,
            "temporal_head": head.version if head else None,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "ATMA-V research videos (CC BY-SA 4.0) — precomputed, not live"}
    timelines = {}
    for v in VIDEOS:
        data = process_video(v, detector, args.infer_fps, head)
        if data:
            data["meta"] = meta
            timelines[v] = data
            print(f"{v}: {len(data['timeline'])} frames, peak score {max(f['s'] for f in data['timeline'])}")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(timelines, f, separators=(",", ":"))
    print(f"wrote {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB) with {meta}")


if __name__ == "__main__":
    main()
