# web/__init__.py
# Flask application factory for the SentinelX platform (local / on-prem mode).

import logging
import logging.handlers
import os
import secrets

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

import config
from core import db as dbm

log = logging.getLogger("sentinelx")


def setup_logging():
    root = logging.getLogger()
    if any(getattr(h, "_sentinelx", False) for h in root.handlers):
        return
    root.setLevel(config.LOG_LEVEL)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh._sentinelx = True
    root.addHandler(sh)
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(os.path.join(config.LOG_DIR, "sentinelx.log"),
                                                  maxBytes=5_000_000, backupCount=5)
        fh.setFormatter(fmt)
        fh._sentinelx = True
        root.addHandler(fh)
    except OSError:
        log.warning("file logging unavailable in %s", config.LOG_DIR)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("ultralytics").setLevel(logging.WARNING)


def ensure_instance_dirs():
    for d in (config.INSTANCE_DIR, config.EVIDENCE_DIR, config.BACKUP_DIR, config.LOG_DIR):
        os.makedirs(d, exist_ok=True)
    for d in (config.EVIDENCE_DIR, config.BACKUP_DIR):
        os.chmod(d, 0o700)


def create_app(start_cameras: bool = True, db_path: str | None = None, testing: bool = False,
               cameras: list[dict] | None = None) -> Flask:
    setup_logging()
    app = Flask(__name__, template_folder=os.path.join(config.BASE_DIR, "templates"),
                static_folder=os.path.join(config.BASE_DIR, "static"))
    db_path = db_path or config.DB_PATH

    secret = config.SECRET_KEY
    if not secret:
        if config.ENV == "production":
            raise RuntimeError("SENTINELX_SECRET_KEY must be set in production")
        secret = secrets.token_hex(32)
        log.warning("SENTINELX_SECRET_KEY not set — using a random key (sessions reset on restart)")
    auth_disabled = config.AUTH_DISABLED
    if auth_disabled and config.ENV == "production":
        raise RuntimeError("SENTINELX_AUTH_DISABLED is not allowed in production")
    if auth_disabled:
        log.warning("AUTHENTICATION DISABLED (development only) — every request acts as admin")

    app.config.update(
        SECRET_KEY=secret, DB_PATH=db_path, TESTING=testing, AUTH_DISABLED=auth_disabled,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE, PERMANENT_SESSION_LIFETIME=12 * 3600,
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024, JSON_SORT_KEYS=False,
        TRUST_PROXY=os.environ.get("SENTINELX_TRUST_PROXY", "") == "1",
        PLATFORM_MODE="live",
    )

    app.json.sort_keys = False          # keep report/model order as written (Flask 3 sorts by default)

    if db_path != ":memory:":
        ensure_instance_dirs()
    applied = dbm.migrate(db_path)
    if applied:
        log.info("applied migrations: %s", applied)

    from web import registry
    registry.seed(db_path)

    from core.alerts import AlertManager
    from core.camera import CameraManager
    from core.runtime_settings import RuntimeSettings
    settings = RuntimeSettings(db_path)
    alerts = AlertManager(settings, db=db_path)
    if cameras is None and not start_cameras:
        cameras = []
    manager = CameraManager(settings, alerts, db=db_path, cameras=cameras)
    app.extensions["sentinelx"] = {"settings": settings, "alerts": alerts, "cameras": manager}

    from web.api import api
    from web.pages import pages
    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.errorhandler(HTTPException)
    def http_error(e):
        if request.path.startswith("/api/") or request.path in ("/threat_data", "/switch_video"):
            return jsonify({"error": e.description, "status": e.code}), e.code
        return e

    @app.errorhandler(Exception)
    def unhandled(e):
        log.exception("unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "internal server error", "status": 500}), 500
        return "Internal server error", 500

    if start_cameras:
        manager.start()
    return app
