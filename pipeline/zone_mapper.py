"""
Zone mapper: maps a bounding box centroid to a zone from store_layout.json.
Uses polygon point-in-polygon test (ray casting).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Zone:
    id: str
    name: str
    type: str
    camera: str
    sku_zone: Optional[str]
    is_staff_zone: bool
    polygon: list[tuple[int, int]]


class ZoneMapper:
    def __init__(self, layout_path: str, camera_id: str):
        with open(layout_path, encoding="utf-8") as f:
            layout = json.load(f)

        self.camera_id = camera_id
        self.zones: list[Zone] = [
            Zone(
                id=z["id"],
                name=z["name"],
                type=z["type"],
                camera=z["camera"],
                sku_zone=z.get("sku_zone"),
                is_staff_zone=z.get("is_staff_zone", False),
                polygon=[(p[0], p[1]) for p in z["polygon_px"]],
            )
            for z in layout["zones"]
            if z["camera"] == camera_id
        ]

    def get_zone(self, cx: float, cy: float) -> Optional[Zone]:
        """Return the zone containing centroid (cx, cy), or None."""
        for zone in self.zones:
            if _point_in_polygon(cx, cy, zone.polygon):
                return zone
        return None

    def get_entry_line_y(self, frame_height: int, fraction: float = 0.75) -> int:
        """Return the Y pixel position of the virtual entry/exit threshold line."""
        return int(frame_height * fraction)


def _point_in_polygon(x: float, y: float, polygon: list[tuple[int, int]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    px, py = x, y
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside
