# app.py
# Flask application — updated for stateful ThreatEngine and new tracker API.

import cv2
import time
import threading
import os
from flask import Flask, Response, render_template, jsonify, request

import config
from core.detector      import Detector
from core.tracker       import PersonTracker
from core.threat_engine import ThreatEngine
from core.event_logger  import EventLogger
from utils.helpers      import (draw_restricted_zone, draw_tracker_info,
                                encode_frame_to_jpeg, get_risk_color_hex)
from data.annotations   import get_video_info, is_anomalous_frame

app = Flask(__name__)

state = {
    "threat":       {"score": 0, "level": "NORMAL", "reasons": [], "details": {}},
    "stats":        {"persons": 0, "bags": 0, "frame": 0},
    "ground_truth": False,
    "video_name":   os.path.basename(config.DEFAULT_VIDEO),
    "log":          [],
}
state_lock = threading.Lock()

detector = Detector()
tracker  = PersonTracker()
engine   = ThreatEngine()       # now a stateful class instance
logger   = EventLogger()

cap      = None
cap_lock = threading.Lock()
video_path = config.DEFAULT_VIDEO


def open_video(path: str):
    global cap, video_path
    tracker.reset()
    engine.reset()              # reset smoothed score on video switch
    logger.reset()
    video_path = path
    with cap_lock:
        if cap:
            cap.release()
        cap = cv2.VideoCapture(path)


def process_frame(frame, frame_number: int):
    h, w = frame.shape[:2]
    video_name = os.path.basename(video_path)

    detections = detector.detect(frame)
    persons    = [d for d in detections if d["class_name"] == "person"]
    bags       = [d for d in detections
                  if d["class_name"] in {"backpack", "handbag", "suitcase"}]

    # Pass frame dimensions to tracker (needed for zone calculations)
    tracked = tracker.update(persons, w, h)

    # Use stateful engine instance
    threat = engine.evaluate(tracked, detections, w, h)

    logger.update(threat["reasons"])
    gt = is_anomalous_frame(video_name, frame_number)

    # Draw zone, tracker info, bag boxes
    draw_restricted_zone(frame, threat["level"])
    draw_tracker_info(frame, tracked)

    for bag in bags:
        x1, y1, x2, y2 = bag["bbox"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), 2)
        cv2.putText(frame, bag["class_name"], (x1, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)

    # Vandalism highlight — red bbox for vandal-flagged persons
    for person in tracked:
        if person.get("vandalism", False):
            x1, y1, x2, y2 = person["bbox"]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
            cv2.putText(frame, "! TAMPERING", (x1, y2 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)

    if gt:
        cv2.putText(frame, "GT: ANOMALOUS", (10, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    color_map = {
        "NORMAL":   (0,200,0), "LOW":    (180,180,0),
        "MEDIUM":   (0,140,255), "HIGH":  (0,0,220),
        "CRITICAL": (0,0,180)
    }
    badge_color = color_map.get(threat["level"], (0,200,0))
    cv2.putText(frame, f'{threat["level"]}  {threat["score"]}',
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, badge_color, 2)

    with state_lock:
        state["threat"]       = threat
        state["ground_truth"] = gt
        state["video_name"]   = video_name
        state["log"]          = logger.get_log()
        state["stats"]        = {
            "persons": len(tracked),
            "bags":    len(bags),
            "frame":   frame_number,
        }

    return frame


def generate_frames():
    frame_delay = 1.0 / config.STREAM_FPS
    while True:
        with cap_lock:
            if cap is None or not cap.isOpened():
                time.sleep(0.1)
                continue
            ret, frame = cap.read()
            frame_number = int(cap.get(cv2.CAP_PROP_POS_FRAMES))

        if not ret:
            with cap_lock:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            tracker.reset()
            engine.reset()
            continue

        frame = process_frame(frame, frame_number)
        jpeg  = encode_frame_to_jpeg(frame)
        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
        time.sleep(frame_delay)


@app.route("/")
def index():
    videos = [f for f in os.listdir(config.VIDEO_DIR) if f.endswith(".mp4")]
    videos.sort()
    return render_template("dashboard.html", videos=videos)

@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/threat_data")
def threat_data():
    with state_lock:
        data = {
            **state["threat"],
            "color":        get_risk_color_hex(state["threat"]["level"]),
            "stats":        state["stats"],
            "ground_truth": state["ground_truth"],
            "log":          state["log"][:15],
        }
    return jsonify(data)

@app.route("/switch_video", methods=["POST"])
def switch_video():
    filename = request.json.get("filename", "")
    path     = os.path.join(config.VIDEO_DIR, filename)
    if os.path.exists(path):
        open_video(path)
        return jsonify({"status": "ok", "video": filename})
    return jsonify({"status": "error", "message": "File not found"}), 404

if __name__ == "__main__":
    open_video(config.DEFAULT_VIDEO)
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT,
            debug=False, threaded=True)