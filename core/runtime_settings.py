# core/runtime_settings.py
# Operator-tunable thresholds. Defaults come from config.py; admins can
# override them at runtime (persisted in the `settings` table, audited).

import threading

import config
from core import repository

SEVERITY_CHOICES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# key: (type, default, min, max | choices, description)
SCHEMA = {
    "incident_open_score":    (int,   config.INCIDENT_OPEN_SCORE, 1, 100,
                               "Threat score at which an incident is opened"),
    "incident_close_seconds": (float, config.INCIDENT_CLOSE_SECONDS, 1.0, 600.0,
                               "Seconds below the open score before an incident closes"),
    "alert_min_severity":     (str,   config.ALERT_MIN_SEVERITY, SEVERITY_CHOICES, None,
                               "Minimum severity that raises an alert"),
    "alert_cooldown_seconds": (float, config.ALERT_COOLDOWN_SECONDS, 0.0, 3600.0,
                               "Per-camera cooldown between alerts of equal or lower severity"),
    "evidence_clip_seconds":  (float, config.EVIDENCE_CLIP_SECONDS, 0.0, 30.0,
                               "Seconds of pre-event video saved as evidence (0 disables clips)"),
    "webhook_enabled":        (bool,  bool(config.ALERT_WEBHOOK_URL), None, None,
                               "Send alerts to SENTINELX_ALERT_WEBHOOK_URL"),
}


def validate(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if k not in SCHEMA:
            raise ValueError(f"unknown setting {k!r}")
        typ, _default, lo, hi, _desc = SCHEMA[k]
        if typ is bool:
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true/false")
        elif typ is str:
            if v not in lo:
                raise ValueError(f"{k} must be one of {lo}")
        else:
            try:
                v = typ(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number") from None
            if not (lo <= v <= hi):
                raise ValueError(f"{k} must be between {lo} and {hi}")
        out[k] = v
    return out


class RuntimeSettings:
    def __init__(self, db_path: str | None = None):
        self._db = db_path
        self._lock = threading.Lock()
        self._values = {k: spec[1] for k, spec in SCHEMA.items()}
        self.reload()

    def reload(self):
        try:
            stored = repository.get_settings(db=self._db)
        except Exception:     # DB not initialised yet → defaults
            stored = {}
        with self._lock:
            for k, v in stored.items():
                if k in SCHEMA:
                    try:
                        self._values.update(validate({k: v}))
                    except ValueError:
                        pass

    def get(self, key):
        with self._lock:
            return self._values[key]

    def all(self) -> dict:
        with self._lock:
            return dict(self._values)

    def describe(self) -> list[dict]:
        vals = self.all()
        out = []
        for k, (typ, default, lo, hi, desc) in SCHEMA.items():
            item = {"key": k, "value": vals[k], "default": default, "description": desc,
                    "type": typ.__name__}
            if typ is str:
                item["choices"] = list(lo)
            elif typ is not bool:
                item["min"], item["max"] = lo, hi
            out.append(item)
        return out

    def update(self, values: dict, actor: str, ip: str | None = None) -> dict:
        clean = validate(values)
        repository.set_settings(clean, actor, ip, db=self._db)
        with self._lock:
            self._values.update(clean)
        return self.all()
