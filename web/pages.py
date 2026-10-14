# web/pages.py
# HTML pages and backward-compatible v0 endpoints.

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, request

from web.auth import csrf_token, current_user, require

pages = Blueprint("pages", __name__)


def _mgr():
    return current_app.extensions["sentinelx"]["cameras"]


@pages.route("/")
def index():
    user = current_user()
    if user is None:
        return redirect("/login")
    return render_template("dashboard.html", mode="live", user=user, csrf=csrf_token())


@pages.route("/login")
def login_page():
    if current_user() is not None:
        return redirect("/")
    return render_template("login.html", mode="live", csrf=csrf_token())


# ── v0 compatibility (original dashboard API) ──────────────────────────────

def _default_worker():
    mgr = _mgr()
    return next(iter(mgr.workers.values()), None)


@pages.route("/video_feed")
@require("viewer")
def video_feed():
    w = _default_worker()
    if w is None:
        return jsonify({"error": "no camera configured"}), 404
    return redirect(f"/api/cameras/{w.cam['id']}/stream")


@pages.route("/threat_data")
@require("viewer")
def threat_data():
    w = _default_worker()
    if w is None:
        return jsonify({"error": "no camera configured"}), 404
    s = w.snapshot_state()
    return jsonify({**s["threat"], "stats": {"persons": len(s["tracks"]),
                                             "bags": sum(1 for o in s["objects"] if o["class_name"] in
                                                         ("backpack", "handbag", "suitcase")),
                                             "frame": s["frame"]},
                    "ground_truth": bool(s["ground_truth"]), "log": []})


@pages.route("/switch_video", methods=["POST"])
@require("analyst")
def switch_video():
    w = _default_worker()
    data = request.get_json(silent=True) or {}
    try:
        w.switch_video(str(data.get("filename", "")))
    except (ValueError, FileNotFoundError, AttributeError) as e:
        return jsonify({"status": "error", "message": str(e) or "File not found"}), 404
    return jsonify({"status": "ok", "video": data.get("filename")})


@pages.route("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")
