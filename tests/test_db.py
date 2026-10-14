import os
import sqlite3

import pytest

from core import db


def test_migrations_apply_once_and_are_idempotent(tmp_path):
    path = str(tmp_path / "m.db")
    applied = db.migrate(path, backup_dir=str(tmp_path / "b"))
    assert applied == ["0001", "0002"]
    assert db.migrate(path, backup_dir=str(tmp_path / "b")) == []
    with db.connection(path) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"incidents", "evidence_files", "audit_log", "users", "cameras", "model_versions", "datasets"} <= tables


def test_modified_migration_is_refused(tmp_path):
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "0001_a.sql").write_text("CREATE TABLE t (x INTEGER);")
    path = str(tmp_path / "x.db")
    db.migrate(path, directory=str(mig), backup_dir=str(tmp_path / "b"))
    (mig / "0001_a.sql").write_text("CREATE TABLE t (x INTEGER, y INTEGER);")
    with pytest.raises(db.MigrationError, match="modified"):
        db.migrate(path, directory=str(mig), backup_dir=str(tmp_path / "b"))


def test_failed_migration_rolls_back_and_preserves_data(tmp_path):
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "0001_a.sql").write_text("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (42);")
    path = str(tmp_path / "x.db")
    db.migrate(path, directory=str(mig), backup_dir=str(tmp_path / "b"))
    (mig / "0002_bad.sql").write_text("ALTER TABLE t ADD COLUMN y INTEGER; THIS IS NOT SQL;")
    with pytest.raises(db.MigrationError):
        db.migrate(path, directory=str(mig), backup_dir=str(tmp_path / "b"))
    with db.connection(path) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(t)")]
        assert cols == ["x"]                                   # partial change rolled back
        assert c.execute("SELECT x FROM t").fetchone()[0] == 42
        assert "0002" not in db.applied_migrations(c)
    # a pre-migrate backup was taken because the DB already had tables
    assert any("pre-migrate" in f for f in os.listdir(tmp_path / "b"))


def test_backup_and_restore_roundtrip(db_path, tmp_path):
    with db.connection(db_path) as c:
        c.execute("INSERT INTO cameras (id, name, source_type, source_ref) VALUES ('cam-x', 'X', 'file', 'a.mp4')")
    b = db.backup(db_path, str(tmp_path / "bk"), label="t")
    assert oct(os.stat(b).st_mode & 0o777) == "0o600"
    with db.connection(db_path) as c:
        c.execute("DELETE FROM cameras")
    db.restore(b, db_path)
    with db.connection(db_path) as c:
        assert c.execute("SELECT count(*) FROM cameras").fetchone()[0] == 1


def test_foreign_keys_enforced(db_path):
    with db.connection(db_path) as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO evidence_files (incident_id, kind, rel_path, sha256, size_bytes, mime_type) "
                  "VALUES ('nope', 'snapshot', 'a.jpg', 'x', 1, 'image/jpeg')")
