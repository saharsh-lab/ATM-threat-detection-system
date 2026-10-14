# core/evidence.py
# Evidence storage. Media files live on disk under EVIDENCE_DIR (never in the
# database, never under /static, never committed). Files are created with
# owner-only permissions and every file is hashed so tampering is detectable.

import hashlib
import logging
import os
import tempfile
import threading

import cv2

import config
from core import repository

log = logging.getLogger("sentinelx.evidence")
_lock = threading.Lock()


def _safe_component(s: str) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in "-_") or "x"


def resolve(rel_path: str) -> str:
    """Absolute path for a stored evidence file; refuses paths escaping EVIDENCE_DIR."""
    root = os.path.realpath(config.EVIDENCE_DIR)
    full = os.path.realpath(os.path.join(root, rel_path))
    if os.path.commonpath([root, full]) != root:
        raise PermissionError("evidence path escapes evidence directory")
    return full


def _store(data_writer, camera_id: str, incident_id: str, kind: str, ext: str, mime: str, db=None) -> int | None:
    rel_dir = os.path.join(_safe_component(camera_id), incident_id[4:12] if incident_id.startswith("INC-") else "misc")
    with _lock:
        abs_dir = resolve(rel_dir)
        os.makedirs(abs_dir, mode=0o700, exist_ok=True)
        n = sum(1 for f in os.listdir(abs_dir) if f.startswith(incident_id))
        name = f"{_safe_component(incident_id)}_{kind}_{n:02d}.{ext}"
    rel = os.path.join(rel_dir, name)
    full = resolve(rel)
    if not data_writer(full):
        return None
    os.chmod(full, 0o600)
    h = hashlib.sha256()
    with open(full, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return repository.add_evidence(incident_id, kind, rel, h.hexdigest(), os.path.getsize(full), mime, db=db)


def save_snapshot(camera_id: str, incident_id: str, jpeg: bytes, db=None) -> int | None:
    def write(path):
        with open(path, "wb") as f:
            f.write(jpeg)
        return True
    return _store(write, camera_id, incident_id, "snapshot", "jpg", "image/jpeg", db)


def save_clip(camera_id: str, incident_id: str, frames: list, fps: float, db=None) -> int | None:
    """frames: list of BGR ndarrays. Tries H.264 (browser-playable) then MPEG-4."""
    if not frames:
        return None
    h, w = frames[0].shape[:2]

    def write(path):
        for fourcc in ("avc1", "mp4v"):
            fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(path))
            os.close(fd)
            vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*fourcc), max(fps, 1.0), (w, h))
            if not vw.isOpened():
                os.remove(tmp)
                continue
            for fr in frames:
                if fr.shape[:2] == (h, w):
                    vw.write(fr)
            vw.release()
            if os.path.getsize(tmp) > 0:
                os.replace(tmp, path)
                return True
            os.remove(tmp)
        log.warning("no working video codec; clip for %s not saved", incident_id)
        return False
    return _store(write, camera_id, incident_id, "clip", "mp4", "video/mp4", db)


def purge_expired(days: int = config.EVIDENCE_RETENTION_DAYS, actor: str = "system", db=None) -> int:
    """Delete evidence older than the retention period (files + rows, audited)."""
    n = 0
    for ev in repository.list_expired_evidence(days, db=db):
        try:
            os.remove(resolve(ev["rel_path"]))
        except FileNotFoundError:
            pass
        repository.delete_evidence_row(ev["id"], actor, db=db)
        n += 1
    return n
