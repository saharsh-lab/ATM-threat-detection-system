# api/index.py
# Vercel serverless entry point — SentinelX DEMO mode.
#
# Serverless functions cannot run YOLO inference or keep camera streams open,
# so this deployment serves the same dashboard with precomputed detections
# (static/data/threat_timelines.json, produced by export_timeline.py with the
# real pipeline) and committed evaluation reports (static/data/platform.json).
# Everything is labelled "Demo · precomputed" in the UI. No database, no
# evidence, no incident writes. Dependencies: Flask only.

import json
import os

from flask import Flask, jsonify, render_template, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TIMELINES_PATH = os.path.join(STATIC_DIR, "data", "threat_timelines.json")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR, static_url_path="/static")

DEMO_VIDEOS = ["28.mp4", "12.mp4", "30a.mp4", "57.mp4", "3.mp4"]
_cache = {}


def load_timelines():
    if "t" not in _cache:
        try:
            with open(TIMELINES_PATH) as f:
                _cache["t"] = json.load(f)
        except (OSError, ValueError) as e:
            print("Error loading timelines:", e)
            _cache["t"] = {}
    return _cache["t"]


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return resp


@app.route("/static/<path:filename>")
def custom_static(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/")
@app.route("/login")
def index():
    return render_template("dashboard.html", mode="demo", user=None, csrf="")


@app.route("/api/health")
def health():
    return jsonify({"status": "healthy", "mode": "demo", "platform": "Vercel Serverless",
                    "videos": [v for v in DEMO_VIDEOS if v in load_timelines()],
                    "note": "precomputed detections only — no live inference in serverless mode"})


@app.route("/api/timeline")
def get_timeline():
    video = request.args.get("video", DEMO_VIDEOS[0])
    data = load_timelines().get(video)
    if data:
        return jsonify(data)
    return jsonify({"error": "Video timeline not found"}), 404


@app.route("/threat_data")
def threat_data():
    """v0-compatible endpoint: nearest precomputed frame for ?video=&time=."""
    video = request.args.get("video", DEMO_VIDEOS[0])
    time_sec = request.args.get("time", type=float, default=0.0)
    frames = load_timelines().get(video, {}).get("timeline", [])
    if not frames:
        return jsonify({"error": "no demo data", "source_kind": "demo"}), 404
    m = min(frames, key=lambda x: abs(x.get("t", 0) - time_sec))
    return jsonify({"score": m["s"], "level": m["l"], "reasons": m["r"], "ground_truth": m.get("gt", False),
                    "stats": {"persons": m.get("p", 0), "bags": m.get("b", 0), "frame": m.get("f", 0)},
                    "boxes": m.get("boxes", []), "source_kind": "demo"})


@app.route("/api/<path:_rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def not_in_demo(_rest):
    return jsonify({"error": "not available in the serverless demo — run SentinelX locally", "mode": "demo"}), 404


if __name__ == "__main__":
    app.run(port=5002, debug=True)
