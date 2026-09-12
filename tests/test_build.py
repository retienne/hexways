"""Golden and end-to-end tests on the hand-made fixture (tests/fixtures/crossing.osm).

The fixture is a path with no surface tag, a road bridge crossing it, a street
in a tunnel, and two things that must be ignored: a proposed road and a
highway=* node.
"""

import json
import shutil
from pathlib import Path

import h3
import pyarrow.parquet as pq
import pytest

from hexways.build import MANIFEST, Options, build
from hexways.cli import main
from hexways.sources import cache_path, open_file, open_overpass
from hexways.stats import compute, format_report

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "crossing.osm"
GOLDEN = FIXTURES / "crossing.expected.json"
BBOX = (47.399, 8.499, 47.403, 8.501)

CROSSING = h3.latlng_to_cell(47.4, 8.500265, 13)   # where the bridge crosses the path
TUNNEL_START = h3.latlng_to_cell(47.401, 8.5, 13)


def rows_of(out_dir: Path) -> list[dict]:
    files = sorted(out_dir.glob("*.parquet"))
    assert files, "no parquet written"
    rows = []
    for f in files:
        rows.extend(pq.read_table(f).to_pylist())
    return rows


def build_fixture(out_dir: Path, **kwargs) -> list[dict]:
    build(open_file(FIXTURE), out_dir, Options(**kwargs))
    return rows_of(out_dir)


@pytest.fixture(scope="module")
def rows(tmp_path_factory) -> list[dict]:
    return build_fixture(tmp_path_factory.mktemp("golden"))


def test_golden_rows(rows):
    expected = json.loads(GOLDEN.read_text())
    assert rows == expected


def test_rows_are_sorted_and_unique(rows):
    keys = [r["h3_index"] for r in rows]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    for r in rows:
        assert r["parent_12"] == h3.cell_to_parent(r["h3_index"], 12)
        ids = [f["source_id"] for f in r["features"]]
        assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_crossing_cell_holds_both_levels(rows):
    (row,) = [r for r in rows if r["h3_index"] == CROSSING]
    path, bridge = row["features"]
    assert path == {"kind": "path", "structure": "none", "level": 0, "bearing": 90.0,
                    "surface": "unknown", "surface_source": "missing", "on_line": True,
                    "source_id": 100}
    assert bridge == {"kind": "secondary", "structure": "bridge", "level": 1, "bearing": 0.0,
                      "surface": "paved", "surface_source": "tagged", "on_line": True,
                      "source_id": 200}


def test_tunnel_feature(rows):
    (row,) = [r for r in rows if r["h3_index"] == TUNNEL_START]
    (tunnel,) = row["features"]
    assert tunnel["kind"] == "residential"
    assert tunnel["structure"] == "tunnel"
    assert tunnel["level"] == -1                      # explicit layer=-1
    assert tunnel["surface"] == "paved"               # inferred from residential
    assert tunnel["surface_source"] == "inferred"
    assert tunnel["bearing"] == 90.0


def test_ignored_ways_and_nodes(rows):
    ids = {f["source_id"] for r in rows for f in r["features"]}
    assert ids == {100, 200, 300}


def test_corridor_ring_is_complete(rows):
    by_cell = {r["h3_index"]: r for r in rows}
    for r in rows:
        for f in r["features"]:
            if f["on_line"]:
                for n in h3.grid_ring(r["h3_index"], 1):
                    assert any(g["source_id"] == f["source_id"]
                               for g in by_cell[n]["features"])


def test_corridor_zero_marks_everything_on_line(tmp_path):
    rows0 = build_fixture(tmp_path, corridor=0)
    assert all(f["on_line"] for r in rows0 for f in r["features"])
    full = json.loads(GOLDEN.read_text())
    on_line = {r["h3_index"] for r in full if any(f["on_line"] for f in r["features"])}
    assert {r["h3_index"] for r in rows0} == on_line


def test_resolution_12_parent_is_self(tmp_path):
    rows12 = build_fixture(tmp_path, resolution=12)
    assert rows12 and all(r["parent_12"] == r["h3_index"] for r in rows12)


def test_overpass_json_gives_identical_rows(tmp_path, rows):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    shutil.copy(FIXTURES / "crossing.overpass.json", cache_path(BBOX, cache_dir))
    source = open_overpass(BBOX, cache_dir)            # served from the cache, no network
    assert source.osm_timestamp == "2026-01-01T00:00:00Z"
    build(source, tmp_path / "out")
    assert rows_of(tmp_path / "out") == rows


def test_bbox_clips_pbf(tmp_path):
    # only the tunnel (way 300) has a node inside this box
    build(open_file(FIXTURE, (47.4009, 8.4999, 47.4011, 8.5001)), tmp_path)
    ids = {f["source_id"] for r in rows_of(tmp_path) for f in r["features"]}
    assert ids == {300}


def test_manifest_and_stats(tmp_path):
    manifest = build(open_file(FIXTURE), tmp_path)
    assert (tmp_path / MANIFEST).exists()
    assert manifest["ways"] == 3
    assert manifest["cells"] == 69
    assert manifest["resolution"] == 13 and manifest["corridor"] == 1
    assert manifest["network_km"] == pytest.approx(0.11, abs=0.005)

    st = compute(tmp_path)
    assert st.cells == 69
    assert st.features == sum(k * v for k, v in st.per_cell.items())
    assert st.per_cell == {1: 58, 2: 11}
    assert st.multi_level == 11 and st.multi_level_same_kind == 0
    assert st.surface_source == {"missing": 1, "tagged": 1, "inferred": 1}
    assert "cells with >1 level                    : 11" in format_report(st)


def test_refuses_to_clobber(tmp_path):
    build(open_file(FIXTURE), tmp_path)
    with pytest.raises(FileExistsError):
        build(open_file(FIXTURE), tmp_path)
    build(open_file(FIXTURE), tmp_path, overwrite=True)


def test_cli(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["-q", "build", "--pbf", str(FIXTURE), "--out", str(out)]) == 0
    assert "cells           : 69" in capsys.readouterr().out
    assert main(["stats", str(out)]) == 0
    assert "features / cell : 1:84.1%, 2:15.9%" in capsys.readouterr().out
    assert main(["-q", "build", "--pbf", str(FIXTURE), "--out", str(out)]) == 1
    assert main(["-q", "build", "--pbf", str(FIXTURE), "--out", str(out), "--resolution", "9"]) == 2
