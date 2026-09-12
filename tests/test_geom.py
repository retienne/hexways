import math

import pytest

from hexways.geom import LAT_M, bearing, densify, length_km

ORIGIN = (47.4, 8.5)
DLAT_10M = 10 / LAT_M
DLON_10M = 10 / (LAT_M * math.cos(math.radians(47.4)))


def _offset(north_m: float, east_m: float):
    return (ORIGIN[0] + north_m / LAT_M,
            ORIGIN[1] + east_m / (LAT_M * math.cos(math.radians(47.4))))


@pytest.mark.parametrize("north, east, expected", [
    (10, 0, 0.0), (0, 10, 90.0), (-10, 0, 0.0), (0, -10, 90.0),
    (10, 10, 45.0), (-10, -10, 45.0), (10, -10, 135.0), (-10, 10, 135.0),
])
def test_bearing_is_folded_into_half_circle(north, east, expected):
    assert bearing(ORIGIN, _offset(north, east)) == pytest.approx(expected, abs=0.05)


def test_bearing_range():
    for deg in range(0, 360, 7):
        b = bearing(ORIGIN, _offset(math.cos(math.radians(deg)), math.sin(math.radians(deg))))
        assert 0.0 <= b < 180.0


def test_length_km():
    assert length_km([ORIGIN, _offset(1000, 0)]) == pytest.approx(1.0, rel=1e-6)
    assert length_km([ORIGIN, _offset(0, 1000)]) == pytest.approx(1.0, rel=1e-3)
    assert length_km([ORIGIN, _offset(300, 400)]) == pytest.approx(0.5, rel=1e-3)


def test_densify_spacing_and_endpoints():
    end = _offset(100, 0)
    pts = densify([ORIGIN, end], step_m=3.0)
    assert pts[0][:2] == ORIGIN
    assert pts[-1][:2] == pytest.approx(end)
    assert len(pts) == 1 + math.ceil(100 / 3.0)
    for a, b in zip(pts, pts[1:]):
        assert length_km([a[:2], b[:2]]) * 1000 <= 3.0 + 1e-6
    assert all(p[2] == pytest.approx(0.0, abs=1e-9) for p in pts)


def test_densify_short_segment_keeps_vertices():
    end = _offset(0, 1)
    pts = densify([ORIGIN, end], step_m=3.0)
    assert [p[:2] for p in pts] == [ORIGIN, pytest.approx(end)]


def test_densify_bearing_follows_each_segment():
    corner = _offset(20, 0)
    end = _offset(20, 20)
    pts = densify([ORIGIN, corner, end], step_m=5.0)
    north = [p for p in pts if p[1] == pytest.approx(ORIGIN[1])]
    east = [p for p in pts if p[0] == pytest.approx(corner[0]) and p[1] > ORIGIN[1]]
    assert north and east
    assert all(p[2] == pytest.approx(0.0, abs=1e-9) for p in north)
    assert all(p[2] == pytest.approx(90.0, abs=0.05) for p in east)


def test_densify_skips_duplicate_vertices():
    end = _offset(10, 0)
    assert densify([ORIGIN, ORIGIN, end], 3.0) == densify([ORIGIN, end], 3.0)
    assert densify([ORIGIN, ORIGIN], 3.0) == []
