"""The numbers the design rests on, read back from a built output directory.

Everything is computed with numpy over the flattened ``features`` column, so a
country-sized table takes seconds rather than the minutes a row-by-row pass in
Python would need.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .build import MANIFEST


@dataclass
class Stats:
    manifest: dict = field(default_factory=dict)
    files: int = 0
    bytes: int = 0
    cells: int = 0
    features: int = 0
    per_cell: Counter = field(default_factory=Counter)      # features per cell → cells
    on_line_cells: int = 0                                  # cells some line passes through
    structure_cells: int = 0                                # cells touching bridge/tunnel/gallery/ford
    multi_level: int = 0                                    # cells with features on >1 level
    multi_level_same_kind: int = 0                          # …of which a kind repeats across levels
    kinds: Counter = field(default_factory=Counter)         # per feature
    surface: Counter = field(default_factory=Counter)       # per feature
    surface_source: Counter = field(default_factory=Counter)  # per way

    @property
    def multi_level_diff_kind(self) -> int:
        return self.multi_level - self.multi_level_same_kind


def _scan_file(path: Path, st: Stats, way_source: list[pa.Table]) -> None:
    table = pq.read_table(path, columns=["features"])
    st.files += 1
    st.bytes += path.stat().st_size
    feats = table.column("features").combine_chunks()
    n = len(feats)
    if n == 0:
        return
    offsets = feats.offsets.to_numpy()
    starts = offsets[:-1]
    values = feats.values
    st.cells += n
    st.features += len(values)
    st.per_cell.update(Counter(np.diff(offsets).tolist()))

    on_line = values.field("on_line").to_numpy(zero_copy_only=False)
    st.on_line_cells += int(np.logical_or.reduceat(on_line, starts).sum())

    has_structure = pc.not_equal(values.field("structure"), "none").to_numpy(zero_copy_only=False)
    st.structure_cells += int(np.logical_or.reduceat(has_structure, starts).sum())

    level = values.field("level").to_numpy()
    multi = np.minimum.reduceat(level, starts) != np.maximum.reduceat(level, starts)
    st.multi_level += int(multi.sum())

    # Same kind on two levels: sort features by (cell, kind, level) and look for
    # a neighbour with the same cell and kind but a different level.
    kind = values.field("kind")
    kind_id = pc.dictionary_encode(kind).indices.to_numpy()
    cell_id = np.repeat(np.arange(n), np.diff(offsets))
    order = np.lexsort((level, kind_id, cell_id))
    c, k, lv = cell_id[order], kind_id[order], level[order]
    clash = (c[1:] == c[:-1]) & (k[1:] == k[:-1]) & (lv[1:] != lv[:-1])
    st.multi_level_same_kind += len(np.unique(c[1:][clash]))

    st.kinds.update(dict(zip(*(a.to_pylist() for a in pc.value_counts(kind).flatten()))))
    st.surface.update(dict(zip(*(a.to_pylist() for a in
                                 pc.value_counts(values.field("surface")).flatten()))))
    # surface_source is a property of the way, so count it once per way id:
    # keep the distinct pairs from every file and merge them at the end.
    pairs = pa.table({"id": values.field("source_id"), "src": values.field("surface_source")})
    way_source.append(pairs.group_by(["id", "src"]).aggregate([]))


def compute(out_dir: Path) -> Stats:
    st = Stats()
    manifest_path = out_dir / MANIFEST
    if manifest_path.exists():
        st.manifest = json.loads(manifest_path.read_text())
    way_source: list[pa.Table] = []
    for path in sorted(out_dir.glob("*.parquet")):
        _scan_file(path, st, way_source)
    if way_source:
        pairs = pa.concat_tables(way_source).group_by(["id", "src"]).aggregate([])
        st.surface_source.update(dict(zip(*(a.to_pylist() for a in
                                            pc.value_counts(pairs.column("src")).flatten()))))
    return st


def format_report(st: Stats) -> str:
    m = st.manifest
    km = m.get("network_km")
    n = max(st.cells, 1)
    lines = []
    if m:
        lines.append(f"source          : {m.get('source')}  (OSM data {m.get('osm_timestamp')})")
        lines.append(f"settings        : resolution {m.get('resolution')}, corridor "
                     f"{m.get('corridor')}, step {m.get('step_m')} m")
        secs = m.get("seconds", {})
        lines.append(f"ways            : {m.get('ways'):,}   network {km:,.0f} km   "
                     f"build {secs.get('scatter', 0) + secs.get('reduce', 0):,.0f} s")
    lines.append(f"cells           : {st.cells:,}" +
                 (f"   = {st.cells / km:,.0f} cells per km of network" if km else ""))
    lines.append(f"parquet         : {st.bytes / 1e6:,.1f} MB in {st.files} files  "
                 f"= {st.bytes / n:.0f} bytes per cell")
    if st.surface_source:
        total = sum(st.surface_source.values())
        lines.append("surface source  : " + ", ".join(
            f"{k} {v / total * 100:.0f}%" for k, v in st.surface_source.most_common()) +
            "  (per way)")
    if st.surface:
        lines.append("surface         : " + ", ".join(
            f"{k} {v / st.features * 100:.0f}%" for k, v in st.surface.most_common()) +
            "  (per feature)")
    hist = sorted(st.per_cell.items())
    lines.append("features / cell : " + ", ".join(
        f"{k}:{v / n * 100:.1f}%" for k, v in hist[:6]) +
        (f", 7+:{sum(v for k, v in hist[6:]) / n * 100:.1f}%" if len(hist) > 6 else ""))
    lines.append(f"cells on a line                        : {st.on_line_cells:,} "
                 f"({st.on_line_cells / n * 100:.1f}%)")
    lines.append(f"cells touching a structure             : {st.structure_cells:,} "
                 f"({st.structure_cells / n * 100:.1f}%)")
    lines.append(f"cells with >1 level                    : {st.multi_level:,} "
                 f"({st.multi_level / n * 100:.2f}%)")
    lines.append(f"   of which kinds differ across levels : {st.multi_level_diff_kind:,}"
                 "  <- separable by kind x activity alone")
    lines.append(f"   of which same kind on both levels   : {st.multi_level_same_kind:,}"
                 "  <- needs continuity / bearing / altitude")
    return "\n".join(lines)
