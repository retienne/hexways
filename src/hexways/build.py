"""Ways in, Parquet out.

The build has two phases so that a country-sized run never holds all its cells
in memory:

1. **scatter** — stream the ways once; for each way, densify it, collect the
   corridor cells, and append compact records (cell, way index, bearing,
   on_line) to a spill file per *chunk* — the cell's ancestor at
   ``chunk_resolution`` (default 4, ~1,800 km²).
2. **reduce** — for each chunk, load its records, sort by cell, group the
   features per cell, and write one Parquet file. Chunks are independent, so
   this phase parallelises trivially.

Rows within a file are sorted by ``h3_index``; since children of one parent
share a prefix of the integer index, that also sorts them by ``parent_12``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import h3
import h3.api.basic_int as h3i
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import __version__, cells
from .geom import Point, densify, length_km
from .sources import Source
from .tags import SKIP_KINDS, level, structure, surface_class

log = logging.getLogger(__name__)

MANIFEST = "_manifest.json"
TMP_DIR = ".hexways-tmp"

FEATURE_TYPE = pa.struct([
    ("kind", pa.string()),
    ("structure", pa.string()),
    ("level", pa.int8()),
    ("bearing", pa.float32()),
    ("surface", pa.string()),
    ("surface_source", pa.string()),
    ("on_line", pa.bool_()),
    ("source_id", pa.int64()),
])
SCHEMA = pa.schema([
    ("h3_index", pa.string()),
    ("parent_12", pa.string()),
    ("features", pa.list_(FEATURE_TYPE)),
])

# One spill record: 17 bytes, no padding.
RECORD = np.dtype([("cell", "<u8"), ("way", "<u4"), ("bearing", "<f4"), ("on_line", "u1")])
FLUSH_ROWS = 1 << 18


@dataclass(frozen=True)
class Options:
    resolution: int = 13
    corridor: int = 1
    chunk_resolution: int = 4
    workers: int = 1

    def __post_init__(self) -> None:
        if not 12 <= self.resolution <= 15:
            raise ValueError("resolution must be 12..15 (parent_12 must exist)")
        if self.corridor < 0:
            raise ValueError("corridor must be >= 0")
        if not 0 <= self.chunk_resolution < self.resolution:
            raise ValueError("chunk resolution must be below the cell resolution")

    @property
    def step_m(self) -> float:
        # Half a cell edge (~2 m at resolution 13). One full edge already
        # satisfies the design, but a line clipping a cell's corner can slip
        # between two points that far apart; halving the step recovers those
        # cells as on_line at no measurable cost (the h3 lookups are cheap
        # compared with the ring work done once per new cell).
        return h3.average_hexagon_edge_length(self.resolution, unit="m") / 2


# --- scatter -----------------------------------------------------------------


def corridor_cells(parts: list[list[Point]], resolution: int, corridor: int,
                   step_m: float) -> dict[int, tuple[bool, float]]:
    """Cells within ``corridor`` rings of the polylines → (on_line, bearing).

    ``on_line`` is true for cells the line passes through. A ring cell keeps
    the bearing of the first line cell that reached it; a line cell keeps the
    bearing of the first segment that entered it.
    """
    hits: dict[int, tuple[bool, float]] = {}
    for part in parts:
        last = None
        for lat, lon, bearing in densify(part, step_m):
            cell = h3i.latlng_to_cell(lat, lon, resolution)
            if cell == last:
                continue
            last = cell
            hit = hits.get(cell)
            if hit is not None and hit[0]:
                continue
            hits[cell] = (True, bearing)
            if corridor:
                for ring in h3i.grid_disk(cell, corridor):
                    if ring not in hits:
                        hits[ring] = (False, bearing)
    return hits


class _Spill:
    """Append-only record files, one per chunk, buffered in memory."""

    def __init__(self, tmp: Path, shift: int) -> None:
        self.tmp = tmp
        self.shift = shift
        self.buffers: dict[int, list[tuple[int, int, float, bool]]] = defaultdict(list)
        self.rows = 0

    def add(self, way_idx: int, hits: dict[int, tuple[bool, float]]) -> None:
        shift = self.shift
        buffers = self.buffers
        for cell, (on_line, bearing) in hits.items():
            buffers[cell >> shift].append((cell, way_idx, bearing, on_line))
        self.rows += len(hits)
        if self.rows >= FLUSH_ROWS:
            self.flush()

    def flush(self) -> None:
        for key, rows in self.buffers.items():
            if rows:
                with open(self.tmp / f"{key:x}.rec", "ab") as fh:
                    np.array(rows, dtype=RECORD).tofile(fh)
                rows.clear()
        self.rows = 0

    def files(self) -> list[Path]:
        return sorted(self.tmp.glob("*.rec"))


def scatter(source: Source, tmp: Path, opts: Options) -> dict:
    """Phase 1. Returns the counters the manifest reports."""
    columns: dict[str, list] = {k: [] for k in
                                ("kind", "structure", "level", "surface", "surface_source",
                                 "source_id")}
    # Chunk key = the bits of the index above the digits finer than chunk_resolution.
    spill = _Spill(tmp, 3 * (15 - opts.chunk_resolution))
    step = opts.step_m
    seen = 0
    km = 0.0
    t0 = time.time()
    for way in source.ways:
        kind = way.tags["highway"]
        if kind in SKIP_KINDS:
            continue
        hits = corridor_cells(way.parts, opts.resolution, opts.corridor, step)
        if not hits:
            continue
        struct = structure(way.tags)
        surface, surface_source = surface_class(way.tags)
        columns["kind"].append(kind)
        columns["structure"].append(struct)
        columns["level"].append(level(way.tags, struct))
        columns["surface"].append(surface)
        columns["surface_source"].append(surface_source)
        columns["source_id"].append(way.id)
        spill.add(len(columns["kind"]) - 1, hits)
        km += sum(length_km(p) for p in way.parts)
        seen += 1
        if seen % 100_000 == 0:
            log.info("scatter: %s ways, %.0f km, %.0fs", f"{seen:,}", km, time.time() - t0)
    spill.flush()
    pq.write_table(pa.table({
        "kind": pa.array(columns["kind"], pa.string()),
        "structure": pa.array(columns["structure"], pa.string()),
        "level": pa.array(columns["level"], pa.int8()),
        "surface": pa.array(columns["surface"], pa.string()),
        "surface_source": pa.array(columns["surface_source"], pa.string()),
        "source_id": pa.array(columns["source_id"], pa.int64()),
    }), tmp / "ways.parquet")
    return {"ways": seen, "network_km": km, "chunks": len(spill.files())}


# --- reduce ------------------------------------------------------------------


def reduce_chunk(rec_path: Path, ways_path: Path, out_dir: Path, opts: Options) -> tuple[int, int]:
    """Phase 2 for one chunk. Returns (cells, features) written."""
    rec = np.fromfile(rec_path, dtype=RECORD)
    # Sort by cell, then way id (a stable feature order), with on_line first so
    # that dropping later duplicates of a (cell, way) pair keeps the line hit.
    rec = rec[np.lexsort((1 - rec["on_line"], rec["way"], rec["cell"]))]
    cell, way = rec["cell"], rec["way"]
    keep = np.empty(len(rec), dtype=bool)
    keep[0] = True
    np.logical_or(cell[1:] != cell[:-1], way[1:] != way[:-1], out=keep[1:])
    rec = rec[keep]
    cell, way = rec["cell"], rec["way"]

    first = np.empty(len(rec), dtype=bool)
    first[0] = True
    np.not_equal(cell[1:], cell[:-1], out=first[1:])
    starts = np.flatnonzero(first)
    offsets = pa.array(np.append(starts, len(rec)).astype(np.int32))
    unique = cell[starts]

    ways = pq.read_table(ways_path)
    idx = pa.array(way)
    take = lambda name: pc.take(ways.column(name).combine_chunks(), idx)  # noqa: E731
    features = pa.StructArray.from_arrays([
        take("kind"), take("structure"), take("level"),
        pa.array(rec["bearing"]),
        take("surface"), take("surface_source"),
        pa.array(rec["on_line"].astype(bool)),
        take("source_id"),
    ], fields=list(FEATURE_TYPE))
    table = pa.table({
        "h3_index": cells.to_strings(unique),
        "parent_12": cells.to_strings(cells.parent(unique, opts.resolution, 12)),
        "features": pa.ListArray.from_arrays(offsets, features),
    }, schema=SCHEMA)

    chunk = h3.int_to_str(int(cells.parent(unique[:1], opts.resolution, opts.chunk_resolution)[0]))
    pq.write_table(table, out_dir / f"{chunk}.parquet", compression="zstd",
                   row_group_size=65_536, sorting_columns=[pq.SortingColumn(0)])
    return len(unique), len(rec)


def reduce_all(tmp: Path, out_dir: Path, opts: Options) -> tuple[int, int]:
    ways_path = tmp / "ways.parquet"
    rec_files = sorted(tmp.glob("*.rec"))
    n_cells = n_features = 0
    t0 = time.time()
    if opts.workers > 1:
        with ProcessPoolExecutor(opts.workers) as pool:
            results = pool.map(reduce_chunk, rec_files, [ways_path] * len(rec_files),
                               [out_dir] * len(rec_files), [opts] * len(rec_files))
            for i, (c, f) in enumerate(results, 1):
                n_cells += c
                n_features += f
                log.info("reduce: %d/%d chunks, %s cells, %.0fs", i, len(rec_files),
                         f"{n_cells:,}", time.time() - t0)
    else:
        for i, rec_path in enumerate(rec_files, 1):
            c, f = reduce_chunk(rec_path, ways_path, out_dir, opts)
            n_cells += c
            n_features += f
            log.info("reduce: %d/%d chunks, %s cells, %.0fs", i, len(rec_files),
                     f"{n_cells:,}", time.time() - t0)
    return n_cells, n_features


# --- driver ------------------------------------------------------------------


def build(source: Source, out_dir: Path, opts: Options = Options(), overwrite: bool = False) -> dict:
    if out_dir.exists() and any(out_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"{out_dir} is not empty (use --overwrite)")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / TMP_DIR
    tmp.mkdir()
    try:
        t0 = time.time()
        counters = scatter(source, tmp, opts)
        t1 = time.time()
        n_cells, n_features = reduce_all(tmp, out_dir, opts)
        t2 = time.time()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    manifest = {
        "hexways": __version__,
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": source.description,
        "osm_timestamp": source.osm_timestamp,
        "resolution": opts.resolution,
        "corridor": opts.corridor,
        "chunk_resolution": opts.chunk_resolution,
        "step_m": round(opts.step_m, 3),
        **counters,
        "cells": n_cells,
        "features": n_features,
        "seconds": {"scatter": round(t1 - t0, 1), "reduce": round(t2 - t1, 1)},
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
