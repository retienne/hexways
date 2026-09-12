"""Planar geometry on lat/lon polylines.

An equirectangular approximation (metres per degree of latitude, scaled by
cos(lat) for longitude) is accurate to well under a percent over the few
metres between densified points, which is all we need: the error is far
below the size of an H3 resolution-13 cell.
"""

from __future__ import annotations

import math

LAT_M = 111_320.0  # metres per degree of latitude

Point = tuple[float, float]  # (lat, lon)


def _dxdy(a: Point, b: Point) -> tuple[float, float]:
    dy = (b[0] - a[0]) * LAT_M
    dx = (b[1] - a[1]) * LAT_M * math.cos(math.radians((a[0] + b[0]) / 2))
    return dx, dy


def bearing(a: Point, b: Point) -> float:
    """Direction of the segment a→b in degrees clockwise from north, folded into [0, 180).

    A way has no direction of travel, so 10° and 190° are the same line.
    """
    dx, dy = _dxdy(a, b)
    return math.degrees(math.atan2(dx, dy)) % 180.0


def length_km(points: list[Point]) -> float:
    return sum(math.hypot(*_dxdy(a, b)) for a, b in zip(points, points[1:])) / 1000.0


def densify(points: list[Point], step_m: float) -> list[tuple[float, float, float]]:
    """Points at most ``step_m`` apart along the polyline, each with the segment bearing.

    The vertices themselves are always included. Zero-length segments
    (duplicate consecutive nodes, which OSM does contain) are skipped.
    """
    out: list[tuple[float, float, float]] = []
    for a, b in zip(points, points[1:]):
        dx, dy = _dxdy(a, b)
        seg = math.hypot(dx, dy)
        if seg == 0.0:
            continue
        brg = math.degrees(math.atan2(dx, dy)) % 180.0
        if not out:
            out.append((a[0], a[1], brg))
        n = max(1, math.ceil(seg / step_m))
        dlat, dlon = (b[0] - a[0]) / n, (b[1] - a[1]) / n
        out.extend((a[0] + dlat * i, a[1] + dlon * i, brg) for i in range(1, n + 1))
    return out
