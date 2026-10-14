# core/db.py
# SQLite connection management, versioned migrations and online backups.
#
# Migrations are plain SQL files in db/migrations named NNNN_description.sql.
# Each is applied once, inside a transaction, and recorded with its checksum in
# schema_migrations. Editing an applied migration is detected and refused —
# add a new migration instead. A backup is taken before any pending migration
# runs against a database that already has tables.

import hashlib
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

log = logging.getLogger("sentinelx.db")

MIGRATIONS_DIR = os.path.join(config.BASE_DIR, "db", "migrations")
BACKUP_KEEP = 20
_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)   # autocommit; explicit BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def connection(path: str | None = None):
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def discover_migrations(directory: str = MIGRATIONS_DIR) -> list[tuple[str, str, str, str]]:
    """[(version, name, sql, sha256)] sorted by version."""
    out = []
    for fname in sorted(os.listdir(directory)):
        m = _MIGRATION_RE.match(fname)
        if not m:
            continue
        with open(os.path.join(directory, fname), encoding="utf-8") as f:
            sql = f.read()
        out.append((m.group(1), m.group(2), sql, hashlib.sha256(sql.encode()).hexdigest()))
    versions = [v for v, *_ in out]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration versions in {directory}")
    return out


def _ensure_migrations_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL)""")


def applied_migrations(conn) -> dict[str, str]:
    _ensure_migrations_table(conn)
    return {r["version"]: r["checksum"] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}


def pending_migrations(conn, directory: str = MIGRATIONS_DIR) -> list:
    applied = applied_migrations(conn)
    pending = []
    for version, name, sql, checksum in discover_migrations(directory):
        if version in applied:
            if applied[version] != checksum:
                raise MigrationError(
                    f"migration {version}_{name}.sql was modified after being applied; "
                    "create a new migration instead")
            continue
        pending.append((version, name, sql, checksum))
    return pending


def _has_user_tables(conn) -> bool:
    row = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' "
                       "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'").fetchone()
    return row[0] > 0


def migrate(path: str | None = None, directory: str = MIGRATIONS_DIR, backup_dir: str | None = None) -> list[str]:
    """Apply pending migrations. Returns the list of applied versions."""
    path = path or config.DB_PATH
    with connection(path) as conn:
        pending = pending_migrations(conn, directory)
        if not pending:
            return []
        if path != ":memory:" and _has_user_tables(conn):
            backup(path, backup_dir or config.BACKUP_DIR, label="pre-migrate")
        done = []
        for version, name, sql, checksum in pending:
            log.info("applying migration %s_%s", version, name)
            try:
                conn.executescript(
                    "BEGIN;\n" + sql +
                    f"\nINSERT INTO schema_migrations (version, name, checksum, applied_at) "
                    f"VALUES ('{version}', '{name}', '{checksum}', '{utcnow()}');\nCOMMIT;")
            except sqlite3.Error as e:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"migration {version}_{name} failed: {e}") from e
            done.append(version)
        return done


def backup(path: str | None = None, backup_dir: str | None = None, label: str = "manual") -> str:
    """Consistent online backup via the SQLite backup API (safe while in use)."""
    path = path or config.DB_PATH
    backup_dir = backup_dir or config.BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = os.path.join(backup_dir, f"sentinelx-{stamp}-{label}.db")
    src = sqlite3.connect(path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RuntimeError(f"backup integrity check failed: {ok}")
    finally:
        dst.close()
        src.close()
    os.chmod(dest, 0o600)
    _prune_backups(backup_dir)
    log.info("database backup written to %s", dest)
    return dest


def _prune_backups(backup_dir: str, keep: int = BACKUP_KEEP):
    files = sorted(f for f in os.listdir(backup_dir) if f.startswith("sentinelx-") and f.endswith(".db"))
    for f in files[:-keep]:
        os.remove(os.path.join(backup_dir, f))


def restore(backup_path: str, path: str | None = None) -> str:
    """Restore a backup over the live database (a safety backup is taken first)."""
    path = path or config.DB_PATH
    with connection(backup_path) as b:
        if b.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("refusing to restore a backup that fails integrity_check")
    safety = backup(path, label="pre-restore") if os.path.exists(path) else None
    src = sqlite3.connect(backup_path)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return safety or ""


def integrity_check(path: str | None = None) -> str:
    with connection(path) as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
