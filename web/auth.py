# web/auth.py
# Session authentication with role-based access control.
#
# Roles (cumulative): viewer < analyst < admin
#   viewer   dashboards, incident list/detail, camera streams
#   analyst  + change incident status/notes, view & download evidence
#   admin    + runtime settings, audit log, backups
#
# Mutating requests must send the per-session CSRF token in the
# X-CSRF-Token header (sessions use SameSite=Lax HttpOnly cookies).

import hmac
import secrets
import time
from functools import wraps

from flask import current_app, g, request, session
from werkzeug.exceptions import Forbidden, Unauthorized
from werkzeug.security import check_password_hash, generate_password_hash

from core import repository

ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}
SESSION_MAX_AGE = 12 * 3600
MAX_FAILED, FAILED_WINDOW = 8, 300   # brute-force throttle: attempts per IP per window

DEV_USER = {"id": 0, "username": "dev", "role": "admin"}


def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise ValueError("password must be at least 10 characters")
    return generate_password_hash(pw, method="scrypt")


def client_ip() -> str:
    # X-Forwarded-For is only honoured behind a trusted reverse proxy.
    if current_app.config.get("TRUST_PROXY"):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def current_user():
    if "user" in g:
        return g.user
    g.user = None
    if current_app.config.get("AUTH_DISABLED"):
        g.user = DEV_USER
        return g.user
    uid = session.get("uid")
    if uid is None or time.time() - session.get("t", 0) > SESSION_MAX_AGE:
        return None
    u = repository.get_user_by_id(uid, db=current_app.config["DB_PATH"])
    if u and not u["disabled"]:
        g.user = u
    return g.user


def _failed() -> dict:
    """ip -> [timestamps] of failed logins, kept per application instance."""
    return current_app.extensions.setdefault("sentinelx_failed_logins", {})


def throttled(ip: str) -> bool:
    now = time.time()
    failed = _failed()
    hits = [t for t in failed.get(ip, []) if now - t < FAILED_WINDOW]
    failed[ip] = hits
    return len(hits) >= MAX_FAILED


def login(username: str, password: str):
    db = current_app.config["DB_PATH"]
    ip = client_ip()
    if throttled(ip):
        return None, "too many failed attempts; try again later"
    user = repository.get_user(username, db=db) if username else None
    # constant-time-ish: always run a hash check
    ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password or "")
    if not user or not ok or user["disabled"]:
        _failed().setdefault(ip, []).append(time.time())
        repository.record_login(user["id"] if user else 0, username or "?", ip, False, db=db)
        return None, "invalid username or password"
    _failed().pop(ip, None)
    session.clear()
    session["uid"] = user["id"]
    session["t"] = time.time()
    session["csrf"] = secrets.token_urlsafe(32)
    session.permanent = True
    repository.record_login(user["id"], user["username"], ip, True, db=db)
    return user, None


_DUMMY_HASH = generate_password_hash("not-a-real-password", method="scrypt")


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    if current_app.config.get("AUTH_DISABLED"):
        return
    sent = request.headers.get("X-CSRF-Token", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        raise Forbidden("missing or invalid CSRF token")


def require(role: str = "viewer"):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if u is None:
                raise Unauthorized("authentication required")
            if ROLE_RANK[u["role"]] < ROLE_RANK[role]:
                raise Forbidden(f"requires {role} role")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                check_csrf()
            return fn(*a, **kw)
        return wrapper
    return deco
