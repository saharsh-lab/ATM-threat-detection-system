# core/alerts.py
# Severity-gated alert dispatch with per-camera cooldown and de-duplication.
#
#  * Only incidents at or above `alert_min_severity` alert.
#  * Within `alert_cooldown_seconds` of the last alert for a camera, a new
#    alert is suppressed unless it ESCALATES severity or adds new rules.
#  * Channels: dashboard (alerts table, polled by the UI), application log,
#    optional webhook (SENTINELX_ALERT_WEBHOOK_URL). Webhook delivery runs in a
#    background thread and its outcome is recorded — failures never block
#    inference and are never reported as delivered.

import logging
import threading
import time

import requests

import config
from core import repository
from core.db import utcnow
from core.threat_engine import SEVERITY_ORDER, severity_at_least

log = logging.getLogger("sentinelx.alerts")


class AlertManager:
    def __init__(self, settings, db=None, clock=time.monotonic):
        self.settings = settings
        self._db = db
        self._clock = clock
        self._last = {}            # camera_id -> (t, severity, frozenset(rules))
        self._lock = threading.Lock()
        self.suppressed = 0

    def should_alert(self, camera_id: str, severity: str, rules: set) -> bool:
        if not severity_at_least(severity, self.settings.get("alert_min_severity")):
            return False
        now = self._clock()
        rules = frozenset(rules)
        with self._lock:
            last = self._last.get(camera_id)
            if last is not None and now - last[0] < self.settings.get("alert_cooldown_seconds"):
                _t, last_sev, last_rules = last
                escalates = SEVERITY_ORDER[severity] > SEVERITY_ORDER[last_sev]
                if not escalates and rules <= last_rules:
                    self.suppressed += 1
                    return False
                top = severity if escalates else last_sev
                self._last[camera_id] = (now, top, last_rules | rules)
            else:
                self._last[camera_id] = (now, severity, rules)
        return True

    def dispatch(self, incident_id: str | None, camera_id: str, camera_name: str, severity: str,
                 score: int, reasons: list[str], rules: set, source_kind: str) -> bool:
        if not self.should_alert(camera_id, severity, rules):
            return False
        tag = "" if source_kind == "live" else f" [{source_kind.upper()}]"
        message = f"{severity}{tag} — {camera_name}: {'; '.join(reasons[:3]) or 'threat detected'} (score {score})"
        ts = utcnow()
        base = {"incident_id": incident_id, "camera_id": camera_id, "ts": ts, "severity": severity,
                "message": message}
        repository.add_alert({**base, "channel": "dashboard", "delivered": 1}, db=self._db)
        log.warning("ALERT %s", message)
        if self.settings.get("webhook_enabled") and config.ALERT_WEBHOOK_URL:
            payload = {**base, "score": score, "reasons": reasons, "source_kind": source_kind}
            threading.Thread(target=self._webhook, args=(base, payload), daemon=True).start()
        return True

    def _webhook(self, base: dict, payload: dict):
        delivered, error = 0, None
        try:
            r = requests.post(config.ALERT_WEBHOOK_URL, json=payload, timeout=config.ALERT_WEBHOOK_TIMEOUT)
            r.raise_for_status()
            delivered = 1
        except requests.RequestException as e:
            error = str(e)[:500]
            log.error("webhook delivery failed: %s", error)
        try:
            repository.add_alert({**base, "channel": "webhook", "delivered": delivered, "error": error}, db=self._db)
        except Exception:      # noqa: BLE001
            log.exception("could not record webhook result")
