# api/index.py
# Vercel serverless entrypoint for ATM Threat Detection Dashboard

import os
import json
from flask import Flask, render_template, jsonify, request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TIMELINES_PATH = os.path.join(STATIC_DIR, "data", "threat_timelines.json")

app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR,
    static_url_path="/static"
)

# Available demo videos
DEMO_VIDEOS = ["28.mp4", "12.mp4", "30a.mp4", "57.mp4", "3.mp4"]
current_state = {
    "active_video": "28.mp4"
}

def load_timelines():
    if os.path.exists(TIMELINES_PATH):
        try:
            with open(TIMELINES_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            print("Error loading timelines:", e)
    return {}

@app.route("/")
def index():
    return render_template(
        "dashboard.html",
        videos=DEMO_VIDEOS,
        is_vercel=True,
        default_video=current_state["active_video"]
    )

@app.route("/threat_data")
def threat_data():
    video = request.args.get("video", current_state["active_video"])
    frame = request.args.get("frame", type=int, default=0)
    time_sec = request.args.get("time", type=float, default=0.0)

    timelines = load_timelines()
    video_data = timelines.get(video, {})
    frames = video_data.get("timeline", [])

    matched = None
    if frames:
        # Match closest frame by timestamp or frame number
        if time_sec > 0:
            matched = min(frames, key=lambda x: abs(x.get("t", 0) - time_sec))
        elif frame > 0:
            matched = min(frames, key=lambda x: abs(x.get("f", 0) - frame))
        else:
            matched = frames[0]

    color_map = {
        "NORMAL": "#00c800",
        "LOW": "#c8c800",
        "MEDIUM": "#ff8c00",
        "HIGH": "#dc143c",
        "CRITICAL": "#8b0000"
    }

    if matched:
        level = matched.get("l", "NORMAL")
        return jsonify({
            "score": matched.get("s", 0),
            "level": level,
            "reasons": matched.get("r", []),
            "color": color_map.get(level, "#00c800"),
            "stats": {
                "persons": matched.get("p", 0),
                "bags": matched.get("b", 0),
                "frame": matched.get("f", 0)
            },
            "ground_truth": matched.get("gt", False),
            "log": matched.get("log", []),
            "boxes": matched.get("boxes", [])
        })

    return jsonify({
        "score": 0,
        "level": "NORMAL",
        "reasons": [],
        "color": "#00c800",
        "stats": {"persons": 0, "bags": 0, "frame": 0},
        "ground_truth": False,
        "log": ["System initialized (Vercel Cloud Mode)"],
        "boxes": []
    })

@app.route("/switch_video", methods=["POST"])
def switch_video():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    if filename in DEMO_VIDEOS:
        current_state["active_video"] = filename
        return jsonify({"status": "ok", "video": filename})
    return jsonify({"status": "error", "message": "Video not found"}), 404

@app.route("/api/timeline")
def get_timeline():
    video = request.args.get("video", current_state["active_video"])
    timelines = load_timelines()
    data = timelines.get(video)
    if data:
        return jsonify(data)
    return jsonify({"error": "Video timeline not found"}), 404

@app.route("/api/health")
def health():
    return jsonify({
        "status": "healthy",
        "platform": "Vercel Serverless",
        "videos": DEMO_VIDEOS
    })

if __name__ == "__main__":
    app.run(port=5002, debug=True)
