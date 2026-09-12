# hexways

OpenStreetMap ways as H3 cells: an H3-keyed lookup table of road class,
bridges, tunnels, level, bearing and surface.

`hexways` reads the `highway=*` ways of an OpenStreetMap extract and writes
one Parquet row per **H3 cell** (resolution 13 by default, about 8 m across),
listing every way that passes within a one-cell corridor of it: the highway
class, whether it is a bridge, tunnel, gallery or ford, its vertical level,
its bearing, and a collapsed surface class (paved / gravel / dirt / unknown).

The result is a plain key–value table: give it a coordinate, get the ways
under and beside it, with no geometry library and no spatial index at query
time. That suits anything that has many points and wants a fast, offline
answer to *what is at this location?* — enriching a stream of positions,
map-matching a track, or summarising where a set of points lies. It was
built for [one such use](#why-this-exists); nothing in it is specific to it.

## Switzerland, measured

Geofabrik `switzerland-latest.osm.pbf` (546 MB, data of 2026-09-11), default
settings, one process, on a laptop:

```
ways            : 2,029,012   network 287,848 km   build 1,505 s
cells           : 123,391,432   = 429 cells per km of network
parquet         : 1,205.8 MB in 44 files  = 10 bytes per cell
surface source  : tagged 41%, inferred 34%, missing 25%  (per way)
surface         : paved 48%, unknown 22%, gravel 19%, dirt 11%  (per feature)
features / cell : 1:83.9%, 2:10.9%, 3:3.3%, 4:1.1%, 5:0.4%, 6:0.2%, 7+:0.2%
cells on a line                        : 47,762,583 (38.7%)
cells touching a structure             : 1,727,605 (1.4%)
cells with >1 level                    : 1,166,058 (0.95%)
   of which kinds differ across levels : 325,857
   of which same kind on both levels   : 840,201
```

So: **123 million resolution-13 cells, 1.2 GB of zstd Parquet, 25 minutes**
(19 min streaming the ways, 6 min sorting and writing), peak 3.1 GB of
memory. The Swiss `highway=*` network is 288,000 km, not the ~180,000 km
of road statistics — half of it is tracks, paths and footways.

The per-kilometre figure is 429 cells, above the 281 the prototype measured
on 2,400 km of Zurich: in a city, neighbouring ways share their corridor
cells; a lone forest track shares them with nothing. For the same reason
84 % of all cells hold a single feature nationwide, against 40 % in central
Zurich, and the share of cells with features on two levels is 0.95 %
nationwide against 7–10 % around Zurich main station. Of the two-level
cells, most hold the *same* kind on both levels; the bulk of those are the
two ends of every bridge, where the bridge way (level 1) meets the road it
continues as (level 0).

## Install and run

```sh
pip install hexways          # Python >= 3.11; pulls in h3, numpy, pyarrow, osmium

# the real path: a Geofabrik extract
hexways build --pbf switzerland-latest.osm.pbf --out ch/

# development on a small area, fetched from Overpass and cached
hexways build --bbox 47.372,8.530,47.382,8.548 --out zurich-hb/

# the same box clipped out of the extract (no network, same ways as Overpass)
hexways build --pbf switzerland-latest.osm.pbf --bbox 47.372,8.530,47.382,8.548 --out zurich-hb/

hexways stats ch/
```

Options worth knowing:

| flag | default | meaning |
|---|---|---|
| `--resolution` | 13 | H3 cell resolution of the output rows (12–15) |
| `--corridor` | 1 | rings of cells around the line to include; 0 = line cells only |
| `--chunk-resolution` | 4 | one output file per H3 cell of this resolution |
| `--workers` | 1 | processes used to reduce chunks in parallel |
| `--overwrite` | | replace a non-empty output directory |
| `--cache-dir` | `~/.cache/hexways` | where Overpass responses are kept |

## Output contract

A directory of Parquet files plus `_manifest.json`. Rows are sorted by
`h3_index`; because H3 indexes of sibling cells share a prefix, they are
thereby also grouped by `parent_12`, and each row group's statistics carry a
tight `parent_12` range, so a reader can seek to one parent. Each file covers
one resolution-4 cell (~1,800 km²) and is named after it
(`841f8ffffffffff.parquet`); `h3.cell_to_parent(parent_12, 4)` names the file
a parent lives in.

```
h3_index        string   H3 resolution 13 (hex string, as h3.int_to_str gives it)
parent_12       string   h3.cell_to_parent(h3_index, 12): the consumer's chunk key
features        array<struct>
  kind          string   OSM highway=* value, verbatim
  structure     string   none | bridge | tunnel | gallery | ford
  level         int8     OSM layer=* if numeric, else +1 bridge / -1 tunnel / 0
  bearing       float32  direction of the way at this cell, degrees clockwise
                         from north, in [0, 180) — a way has no direction
  surface       string   paved | gravel | dirt | unknown
  surface_source string  tagged | inferred | missing
  on_line       bool     the line crosses this cell (false = corridor ring)
  source_id     int64    the OSM way id, for continuity and for tracing
```

**A cell holds a list, never a scalar.** About half of all cells hold two or
more features (a road and the footway beside it), and a few percent hold
features on two different levels (a path under a motorway bridge). The table
does not pick one; it carries what a consumer needs to pick — see
[Choosing among features](#choosing-among-features).

Rules, in the order they apply:

- **Ways indexed:** every way with `highway=*` and at least two located
  nodes, except `proposed`, `planned`, `razed` and `demolished`, which do not
  exist on the ground. `highway=*` nodes (bus stops) and relations are not
  indexed. `kind` is otherwise verbatim, so `platform`, `steps`, `corridor`,
  `construction`, … all appear; filtering by kind is the consumer's job.
- **Corridor:** each way is densified to a point every half cell edge
  (~2 m at resolution 13); the cell of every point is marked `on_line`, and
  its 1-ring (`--corridor`) is added with `on_line = false`. A GPS point 3 m
  off a path therefore still hits the path. Where a way revisits a cell, the
  first hit's bearing is kept.
- **structure:** `bridge=*` (not `no`) → `bridge`; else `tunnel=*` → `tunnel`,
  except `tunnel=avalanche_protector` → `gallery` (that is how Swiss avalanche
  galleries are tagged, and they are open on one side); else `covered=*` →
  `gallery`; else `ford=*` → `ford`.
- **level:** `layer=*` when it parses as an integer (clamped to int8), else
  +1 for a bridge, −1 for a tunnel, 0 otherwise. One Swiss bridge in ten has
  no `layer` tag; the default is what puts it above the road it crosses.
- **surface:** the first value of `surface=*` (before any `;`), lower-cased,
  collapsed by the tables in [`tags.py`](src/hexways/tags.py):
  - *paved:* asphalt, paved, concrete, concrete:plates, concrete:lanes,
    paving_stones, sett, cobblestone, unhewn_cobblestone, metal, wood, bricks,
    chipseal, and the rarer grass_paver, metal_grid, flagstone, stone_plates,
    stone_slabs, stone:plates, stone_blocks, cobblestone:flattened,
    interlock, cement, asphalt:lanes, tartan, rubber, acrylic, tiles,
    plastic, plastic_grate
  - *gravel:* gravel, fine_gravel, compacted, pebblestone, rock, grit, stone,
    stones, rocks, rocky, bare_rock, scree, loose_fine_gravel
  - *dirt:* dirt, earth, ground, mud, sand, grass, clay, woodchips, unpaved,
    snow, ice, soil, dirt/sand
  - any other tagged value → `unknown` with `surface_source = tagged`
  - no tag: `paved` (*inferred*) for motorway, trunk, primary, secondary,
    tertiary and their `_link`s, residential, living_street, service,
    unclassified, cycleway, pedestrian; else from `tracktype`: grade1 →
    paved, grade2–3 → gravel, grade4–5 → dirt (*inferred*); else `unknown`
    with `surface_source = missing`. Footway, path, track and steps are
    deliberately not assumed paved: in Switzerland they are as often forest
    floor as asphalt.
- **bearing:** of the segment the densified point lies on, folded into
  [0, 180). Ring cells carry the bearing of the line cell that reached them.

### Reading the output

```python
import pyarrow.dataset as ds
table = ds.dataset("ch/", format="parquet").to_table(
    filter=ds.field("parent_12") == "8c1f8ed9425b7ff")
```

Any Parquet reader works; `_manifest.json` records the source file, the OSM
data timestamp, the settings and the counts.

## Choosing among features

The table lists candidates; the consumer decides which one a point is on.
For a stream of positions, the signals that have worked, as guidance rather
than code:

1. **Kind × context plausibility.** A pedestrian on `motorway`, a car on
   `path`: implausible. A small prior table for the context at hand
   (activity, vehicle type) removes most of the ambiguity — most two-level
   cells hold *different* kinds on the two levels (a motorway over a footway).
2. **Continuity via `source_id`.** The way the previous points were on is the
   way this point is most likely on. Prefer candidates whose `source_id`
   matches recent history.
3. **Heading versus `bearing`.** The direction of travel (from consecutive
   fixes) should agree with the way's bearing modulo 180°. At a crossing the
   two ways differ by ~90°, which settles it.
4. **Altitude as one vote, when present.** A bridge (`level` ≥ 1) and the
   road beneath it are 5–20 m apart; barometric altitude can tell them apart,
   GNSS altitude usually cannot. Treat it as a vote, not a verdict.
5. **`on_line` as a tiebreak.** All else equal, a feature whose line crosses
   the cell beats one only in the ring.

Cells at the two ends of a bridge legitimately hold the same kind at two
levels (the bridge way and the road it continues as); there is nothing to
disambiguate there, the point is at the bridge end.

## How it works

The build has two phases so that a country never has to fit in memory:

1. **scatter** — ways are streamed once (pyosmium with a node-location
   cache, or a cached Overpass response); each way is densified, its corridor
   cells collected, and compact 17-byte records (cell, way, bearing, on_line)
   appended to a spill file per resolution-4 chunk.
2. **reduce** — each chunk is sorted by cell, grouped, joined to the per-way
   attributes and written as one Parquet file. Chunks are independent, hence
   `--workers`.

## Why this exists

I built this for a sports data lakehouse that enriches GPS telemetry at
1 Hz: per sample, *what is the athlete standing on, is it a bridge or a
tunnel, and what is the surface?* The lakehouse already served an elevation
table keyed by H3 resolution 12, so the natural shape was a second table
beside it, keyed by the finer resolution and co-loadable through the
resolution-12 parent — one key lookup per sample inside a streaming job, no
geometry at query time.

Resolution 13 because 12 (22 m across) blurs a path, the road beside it and
the riverbank into one cell, while 14 is finer than GPS error and costs 7×
the cells. A one-cell corridor because a point 3 m off a path should still
find it. A list per cell rather than a winner because, measured on Zurich,
half the cells hold two or more ways and a few percent hold ways on two
levels, and only the consumer knows enough (activity, history, heading) to
choose.

It is a separate project for a licensing reason too — see below.

## Licence

The **code** is released under the [Apache License 2.0](LICENSE).

The **output** of this tool is a derivative database of OpenStreetMap data,
and OpenStreetMap data is licensed under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Anyone who uses, publishes or distributes the tables this tool produces must:

- attribute **© OpenStreetMap contributors** and link to the ODbL, and
- if they publicly use a derivative database, offer it under the ODbL — or,
  as ODbL §4.6(b) allows, offer the *method of making the alterations* to
  OpenStreetMap instead.

Publishing this tool under a permissive licence is how its author satisfies
§4.6(b) for the tables built with it: this repository *is* the algorithm. The
tool is kept deliberately narrow for the same reason — it takes OpenStreetMap
in and writes cells out, and nothing else lives here — so that anything a
consumer builds beside the output stays a *collective* database and
share-alike reaches only the table this tool produces.
