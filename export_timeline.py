# export_timeline.py
# Precomputes real YOLOv8 detections, tracking, and threat scores
# for web/serverless deployments.

import cv2
import os
import json
import config
from core.detector import Detector
from core.tracker import PersonTracker
from core.threat_engine import ThreatEngine
from core.event_logger import EventLogger
from data.annotations import is_anomalous_frame

VIDEOS = ["28.mp4", "12.mp4", "30a.mp4", "57.mp4", "3.mp4"]
OUTPUT_FILE = os.path.join(config.BASE_DIR, "static", "data", "threat_timelines.json")

def process_video(video_name, detector):
    video_path = os.path.join(config.BASE_DIR, "static", "videos", video_name)
    if not os.path.exists(video_path):
        print(f"Video {video_path} not found!")
        return None

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    tracker = PersonTracker()
    engine = ThreatEngine()
    logger = EventLogger()

    frames_data = []
    frame_idx = 0

    print(f"Processing {video_name} ({total_frames} frames, {w}x{h} @ {fps:.1f} fps)...")

    # Sample rate: capture every 2 frames for smooth web playback and compact file size
    SAMPLE_STEP = 2

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        if frame_idx % SAMPLE_STEP != 0 and frame_idx != 1 and frame_idx != total_frames:
            continue

        detections = detector.detect(frame)
        persons = [d for d in detections if d["class_name"] == "person"]
        bags = [d for d in detections if d["class_name"] in {"backpack", "handbag", "suitcase"}]

        tracked = tracker.update(persons, w, h)
        threat = engine.evaluate(tracked, detections, w, h)
        logger.update(threat["reasons"])
        gt = is_anomalous_frame(video_name, frame_idx)

        # Build clean bounding box overlays
        boxes = []
        for p in tracked:
            boxes.append({
                "bbox": [int(x) for x in p["bbox"]],
                "label": f"ID {p['track_id']}",
                "type": "tampering" if p.get("vandalism") else "person",
                "dwell": p.get("duration_sec", 0)
            })

        for b in bags:
            boxes.append({
                "bbox": [int(x) for x in b["bbox"]],
                "label": b["class_name"],
                "type": "bag",
                "dwell": 0
            })

        time_sec = round(frame_idx / fps, 2)
        frames_data.append({
            "t": time_sec,
            "f": frame_idx,
            "s": threat["score"],
            "l": threat["level"],
            "r": threat["reasons"],
            "gt": gt,
            "p": len(tracked),
            "b": len(bags),
            "boxes": boxes,
            "log": logger.get_log()[:5]
        })

    cap.release()
    return {
        "video": video_name,
        "width": w,
        "height": h,
        "fps": fps,
        "total_frames": total_frames,
        "duration": round(total_frames / fps, 2),
        "timeline": frames_data
    }

def main():
    detector = Detector()
    timelines = {}
    for v in VIDEOS:
        data = process_video(v, detector)
        if data:
            timelines[v] = data

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(timelines, f)
    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\nDone! Exported timelines for {len(timelines)} videos to {OUTPUT_FILE} ({size_kb:.1f} KB)")

if __name__ == "__main__":
    main()
