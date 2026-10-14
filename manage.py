#!/usr/bin/env python
"""
SentinelX management commands.

  python manage.py init                       migrate DB, create first admin (prompts)
  python manage.py migrate                    apply pending migrations (backs up first)
  python manage.py create-user NAME --role analyst
  python manage.py backup                     consistent online backup → instance/backups/
  python manage.py restore FILE               restore a backup (takes a safety backup first)
  python manage.py check                      integrity check + migration status
  python manage.py purge-evidence [--days N]  delete evidence older than the retention period
  python manage.py export-static              write static/data/platform.json + report figures
"""

import argparse
import getpass
import os
import sys

import config


def _password(args) -> str:
    pw = os.environ.get("SENTINELX_NEW_USER_PASSWORD")   # non-interactive provisioning
    if pw:
        return pw
    pw = getpass.getpass("Password (min 10 chars): ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("passwords do not match")
    return pw


def cmd_migrate(args):
    from core import db
    from web import ensure_instance_dirs
    ensure_instance_dirs()
    applied = db.migrate()
    print(f"applied: {applied}" if applied else "database is up to date")


def cmd_create_user(args):
    from core import repository
    from web.auth import hash_password
    cmd_migrate(args)
    try:
        repository.create_user(args.username, hash_password(_password(args)), args.role, actor="cli")
    except ValueError as e:
        sys.exit(str(e))
    except Exception as e:   # sqlite3.IntegrityError
        sys.exit(f"could not create user: {e}")
    print(f"created {args.role} {args.username}")


def cmd_init(args):
    from core import repository
    cmd_migrate(args)
    from web import registry
    registry.seed()
    registry.export_static()
    if repository.count_users() == 0:
        print("No users yet — creating the first admin account.")
        args.username = args.username or input("Admin username: ").strip()
        args.role = "admin"
        cmd_create_user(args)
    else:
        print("users already exist; use create-user to add more")


def cmd_backup(args):
    from core import db
    print(db.backup(label="cli"))


def cmd_restore(args):
    from core import db
    safety = db.restore(args.file)
    print(f"restored {args.file}" + (f" (previous database saved to {safety})" if safety else ""))


def cmd_check(args):
    from core import db
    with db.connection() as conn:
        pending = db.pending_migrations(conn)
        applied = db.applied_migrations(conn)
    print(f"database: {config.DB_PATH}")
    print(f"integrity: {db.integrity_check()}")
    print(f"applied migrations: {sorted(applied)}; pending: {[v for v, *_ in pending]}")


def cmd_purge(args):
    from core import evidence
    print(f"purged {evidence.purge_expired(args.days, actor='cli')} evidence files older than {args.days} days")


def cmd_export(args):
    from web import registry
    print(registry.export_static())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--username"); s.set_defaults(fn=cmd_init)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    s = sub.add_parser("create-user"); s.add_argument("username")
    s.add_argument("--role", choices=["viewer", "analyst", "admin"], default="viewer"); s.set_defaults(fn=cmd_create_user)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore"); s.add_argument("file"); s.set_defaults(fn=cmd_restore)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    s = sub.add_parser("purge-evidence"); s.add_argument("--days", type=int, default=config.EVIDENCE_RETENTION_DAYS)
    s.set_defaults(fn=cmd_purge)
    sub.add_parser("export-static").set_defaults(fn=cmd_export)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
