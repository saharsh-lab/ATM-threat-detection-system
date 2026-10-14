# Test configuration: every runtime path points into a throwaway directory
# BEFORE config.py is imported, so tests never touch instance/ data.
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="sentinelx-test-")
os.environ.update({
    "SENTINELX_INSTANCE_DIR": _TMP,
    "SENTINELX_DB_PATH": os.path.join(_TMP, "test.db"),
    "SENTINELX_EVIDENCE_DIR": os.path.join(_TMP, "evidence"),
    "SENTINELX_BACKUP_DIR": os.path.join(_TMP, "backups"),
    "SENTINELX_LOG_DIR": os.path.join(_TMP, "logs"),
    "SENTINELX_SECRET_KEY": "test-secret-key",
    "SENTINELX_CAMERAS_FILE": os.path.join(_TMP, "no-cameras.yaml"),
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    from core import db
    path = str(tmp_path / "sentinelx.db")
    db.migrate(path, backup_dir=str(tmp_path / "backups"))
    return path


@pytest.fixture
def app(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "EVIDENCE_DIR", str(tmp_path / "evidence"))
    os.makedirs(config.EVIDENCE_DIR, exist_ok=True)
    from web import create_app
    application = create_app(start_cameras=False, db_path=str(tmp_path / "app.db"), testing=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(app, username="alice", role="analyst", password="correct-horse-battery"):
    from core import repository
    from web.auth import hash_password
    repository.create_user(username, hash_password(password), role, db=app.config["DB_PATH"])
    return username, password


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["csrf"]


class FakeSettings:
    def __init__(self, **over):
        from core.runtime_settings import SCHEMA
        self.v = {k: s[1] for k, s in SCHEMA.items()}
        self.v.update(over)

    def get(self, k):
        return self.v[k]


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t
