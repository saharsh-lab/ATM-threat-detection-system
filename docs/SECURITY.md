# Security & privacy

Surveillance footage identifies people. SentinelX treats every frame, snapshot
and clip as sensitive personal data.

## Access control
- **Authentication is on by default.** Accounts live in the `users` table with scrypt password hashes
  (Werkzeug); create them with `python manage.py create-user NAME --role ROLE`. There are no default
  credentials. `SENTINELX_AUTH_DISABLED=1` exists for local development only and is refused when
  `SENTINELX_ENV=production`.
- **Roles** (cumulative): `viewer` (dashboards, incident list, camera streams) → `analyst` (review status,
  notes, **evidence**, CSV export, video upload/switch) → `admin` (thresholds, audit log, backups).
- **Sessions:** HttpOnly, SameSite=Lax cookies, 12-hour lifetime, `Secure` in production. A production
  deployment refuses to start without `SENTINELX_SECRET_KEY`.
- **CSRF:** every mutating request must carry the per-session token in `X-CSRF-Token`.
- **Brute force:** 8 failed logins per IP per 5 minutes are throttled; failures are audited.
  `X-Forwarded-For` is honoured only with `SENTINELX_TRUST_PROXY=1`.
- **Network:** the server binds to `127.0.0.1` by default. Expose it through a TLS-terminating reverse proxy,
  never directly.

## Evidence handling
- Snapshots and clips are written to `SENTINELX_EVIDENCE_DIR` (default `instance/evidence`, mode `0700`,
  files `0600`), **never** under `static/` and never in the database or git.
- Each file's SHA-256 is stored in `evidence_files`, so tampering is detectable.
- Evidence is only served by `GET /api/evidence/<id>` to analysts/admins, with `Cache-Control: private, no-store`.
  Every view is written to the audit log. Paths are resolved inside the evidence root, and a tampered DB row
  that points elsewhere is refused.
- **Retention:** `python manage.py purge-evidence --days N` (default `SENTINELX_EVIDENCE_RETENTION_DAYS=30`)
  deletes old files and rows, and audits each deletion. Schedule it with cron/launchd.

## Audit trail
`audit_log` records logins (success and failure), logouts, incident open/close, every status/notes/assignee
change (with before/after values), evidence views, exports, video uploads and switches, settings changes and
backups, together with the actor and client IP.

## Secrets & configuration
- Secrets come from environment variables or a git-ignored `.env` file (template: `.env.example`).
- Network camera URLs (which usually embed credentials) are referenced **by environment-variable name**
  (`url_env`) in `config/cameras.yaml`; the database stores only that name.
- `.gitignore` excludes `instance/` (database, WAL files, evidence, backups, logs, uploads), `.env*`, local
  camera config, raw datasets and training checkpoints.

## Web hardening
Content-Security-Policy (`script-src 'self'`, no inline scripts), `X-Frame-Options: DENY`, `nosniff`,
`Referrer-Policy: same-origin`, and `no-store` on API responses. All SQL is parameterised. Free-text search
goes through a sanitised FTS5 query, and untrusted text is inserted into the DOM with `textContent` only.

## Responsible use
SentinelX produces **decision support for trained operators**, not automated enforcement. The rule engine and
detectors have measured error rates (see `reports/`); every alert must be reviewed by a person. The platform
does not perform face recognition or identity matching. Check local surveillance, data-protection
(e.g. GDPR / DPDP Act) and signage requirements before deploying.

## Reporting a vulnerability
Open a private security advisory on the GitHub repository rather than a public issue.
