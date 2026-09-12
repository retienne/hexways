"""Way sources: a PBF/XML file via pyosmium, or Overpass for small development areas.

Both yield the same :class:`Way` objects, so everything downstream is shared and
the two inputs produce identical output for the same ways.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import osmium

from . import __version__
from .geom import Point
from .tags import TAG_KEYS

log = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]  # south, west, north, east

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


@dataclass(slots=True)
class Way:
    id: int
    tags: dict[str, str]
    # Polylines of (lat, lon). A way has one part unless some of its nodes have
    # no known location (they fall outside the extract); it is then split there
    # rather than joined across the gap.
    parts: list[list[Point]] = field(default_factory=list)


@dataclass(slots=True)
class Source:
    """A stream of ways plus the provenance the manifest records."""

    ways: Iterator[Way]
    description: str
    osm_timestamp: str | None = None


def _in_bbox(bbox: BBox, lat: float, lon: float) -> bool:
    s, w, n, e = bbox
    return s <= lat <= n and w <= lon <= e


def _split_parts(coords: list[Point | None]) -> list[list[Point]]:
    parts: list[list[Point]] = []
    current: list[Point] = []
    for c in coords:
        if c is None:
            if len(current) >= 2:
                parts.append(current)
            current = []
        else:
            current.append(c)
    if len(current) >= 2:
        parts.append(current)
    return parts


def _touches(parts: list[list[Point]], bbox: BBox | None) -> bool:
    if bbox is None:
        return True
    return any(_in_bbox(bbox, la, lo) for part in parts for la, lo in part)


# --- PBF ---------------------------------------------------------------------


def _iter_file(path: Path, bbox: BBox | None) -> Iterator[Way]:
    # KeyFilter drops everything without highway=* before it reaches Python;
    # node locations are cached regardless, which is what with_locations is for.
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for obj in processor:
        coords: list[Point | None] = []
        for node in obj.nodes:
            loc = node.location
            coords.append((loc.lat, loc.lon) if loc.valid() else None)
        parts = _split_parts(coords)
        if not parts or not _touches(parts, bbox):
            continue
        tags = obj.tags
        yield Way(obj.id, {k: tags[k] for k in TAG_KEYS if k in tags}, parts)


def open_file(path: Path, bbox: BBox | None = None) -> Source:
    """Stream ``highway=*`` ways from an .osm.pbf (or .osm XML) file.

    With ``bbox``, only ways with at least one node inside it are kept — the
    same rule Overpass applies, so a PBF and an Overpass run on the same box
    index the same ways.
    """
    header = osmium.io.Reader(str(path)).header()
    stamp = header.get("osmosis_replication_timestamp") or None
    return Source(_iter_file(path, bbox), f"file:{path.name}", stamp)


# --- Overpass ----------------------------------------------------------------


def _overpass_query(bbox: BBox) -> str:
    return f'[out:json][timeout:180];way["highway"]({",".join(map(str, bbox))});out geom;'


def _fetch(query: str, attempts: int = 4) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt in range(attempts):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        req = urllib.request.Request(url, data=body,
                                     headers={"User-Agent": f"hexways/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            # Public Overpass instances answer 504/429 when busy; wait and try
            # the other mirror rather than failing a whole run on one hiccup.
            last = exc
            wait = 10 * (attempt + 1)
            log.warning("Overpass %s failed (%s); retrying in %ss", url, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Overpass failed after {attempts} attempts: {last}")


def fetch_overpass(bbox: BBox, cache_dir: Path) -> tuple[dict, Path]:
    """Return the Overpass JSON for ``bbox``, from ``cache_dir`` if already fetched."""
    query = _overpass_query(bbox)
    key = hashlib.sha1(query.encode()).hexdigest()[:12]
    cache = cache_dir / f"overpass-{'_'.join(map(str, bbox))}-{key}.osm.json"
    if cache.exists():
        log.info("using cached Overpass response %s", cache)
        return json.loads(cache.read_text()), cache
    log.info("fetching %s from Overpass", bbox)
    data = _fetch(query)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data, cache


def _iter_overpass(data: dict) -> Iterator[Way]:
    for element in data.get("elements", ()):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        coords: list[Point | None] = [
            (g["lat"], g["lon"]) if g else None for g in element["geometry"]
        ]
        parts = _split_parts(coords)
        if not parts:
            continue
        yield Way(element["id"], {k: tags[k] for k in TAG_KEYS if k in tags}, parts)


def open_overpass(bbox: BBox, cache_dir: Path) -> Source:
    data, _ = fetch_overpass(bbox, cache_dir)
    stamp = data.get("osm3s", {}).get("timestamp_osm_base")
    return Source(_iter_overpass(data), f"overpass:{','.join(map(str, bbox))}", stamp)
