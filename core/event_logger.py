# core/event_logger.py
# Maintains a timestamped in-memory log of threat events.
# Sent to the dashboard via /threat_data API.

from datetime import datetime
from collections import deque


class EventLogger:
    def __init__(self, max_events: int = 50):
        # deque automatically drops oldest events beyond max_events
        self._log = deque(maxlen=max_events)
        self._last_reasons = set()   # track what was already logged

    def update(self, reasons: list[str]):
        """
        Compare new reasons against previous frame's reasons.
        Log only newly triggered events to avoid flooding.
        """
        new_reasons = set(reasons)
        for reason in new_reasons - self._last_reasons:
            self._log.appendleft({
                "time":    datetime.now().strftime("%I:%M:%S %p"),
                "message": reason,
            })
        self._last_reasons = new_reasons

    def get_log(self) -> list[dict]:
        """Return current log as a list (newest first)."""
        return list(self._log)

    def reset(self):
        self._log.clear()
        self._last_reasons = set()