# core/zones.py
# Per-camera zone geometry. Zones are polygons in normalised (0-1) image
# coordinates so they survive resolution changes.

from dataclasses import dataclass, field

import config


def point_in_polygon(x: float, y: float, poly: list) -> bool:
    """Ray-casting point-in-polygon test (poly: [(x, y), ...])."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def rect_to_polygon(rect) -> list:
    x1, y1, x2, y2 = rect
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def validate_polygon(poly) -> list:
    pts = [(float(p[0]), float(p[1])) for p in poly]
    if len(pts) < 3:
        raise ValueError("a zone polygon needs at least 3 points")
    if any(not (0.0 <= v <= 1.0) for p in pts for v in p):
        raise ValueError("zone coordinates must be normalised to [0, 1]")
    return pts


@dataclass
class ZoneConfig:
    # Area in front of the ATM where a customer stands. Dwell, multiple-person
    # and object rules are evaluated against it.
    atm_zone: list = field(default_factory=lambda: rect_to_polygon(config.RESTRICTED_ZONE))
    # Optional areas nobody should enter (service door, cash cassette panel...)
    restricted: list = field(default_factory=list)
    loitering_seconds: float = config.LOITERING_THRESHOLD_SECONDS

    @classmethod
    def from_dict(cls, d: dict | None) -> "ZoneConfig":
        d = d or {}
        z = cls()
        if d.get("atm_zone"):
            z.atm_zone = validate_polygon(d["atm_zone"])
        z.restricted = [validate_polygon(p) for p in d.get("restricted", [])]
        if d.get("loitering_seconds") is not None:
            z.loitering_seconds = float(d["loitering_seconds"])
        return z

    def to_dict(self) -> dict:
        return {"atm_zone": [list(p) for p in self.atm_zone],
                "restricted": [[list(p) for p in poly] for poly in self.restricted],
                "loitering_seconds": self.loitering_seconds}

    @staticmethod
    def anchor(bbox, frame_w: int, frame_h: int) -> tuple[float, float]:
        """Normalised reference point of a person: centre of the lower half,
        which tracks where they stand better than the bbox centre."""
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2 / frame_w, (y1 + 3 * y2) / 4 / frame_h)

    def in_atm_zone(self, bbox, frame_w, frame_h) -> bool:
        return point_in_polygon(*self.anchor(bbox, frame_w, frame_h), self.atm_zone)

    def in_restricted(self, bbox, frame_w, frame_h) -> bool:
        x, y = self.anchor(bbox, frame_w, frame_h)
        return any(point_in_polygon(x, y, p) for p in self.restricted)

    def contains_center(self, bbox, frame_w, frame_h) -> bool:
        x1, y1, x2, y2 = bbox
        return point_in_polygon((x1 + x2) / 2 / frame_w, (y1 + y2) / 2 / frame_h, self.atm_zone)
