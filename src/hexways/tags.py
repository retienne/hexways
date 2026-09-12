"""Map OSM tags to the per-feature attributes of the output contract.

Everything here is a pure function of a way's tag dictionary so it can be
unit-tested without geometry. The collapse tables are deliberately small: the
consumer needs "paved / gravel / dirt", not the 200 distinct surface values
OpenStreetMap contains.
"""

from __future__ import annotations

from collections.abc import Mapping

# The tag keys a feature depends on. Sources copy only these out of a way, which
# keeps the per-way work small when streaming a country-sized extract.
TAG_KEYS = ("highway", "surface", "tracktype", "bridge", "tunnel", "covered", "ford", "layer")

# Ways whose highway=* value denotes something that does not exist on the
# ground. A GPS point can never be "on" a proposed road, so indexing it would
# only add noise next to the paths that do exist.
SKIP_KINDS = frozenset({"proposed", "planned", "razed", "demolished"})

PAVED = frozenset({
    "asphalt", "paved", "concrete", "concrete:plates", "concrete:lanes",
    "paving_stones", "sett", "cobblestone", "unhewn_cobblestone", "metal", "wood",
    "bricks", "chipseal",
    # also present in the Swiss extract (each < 1,000 ways)
    "grass_paver", "metal_grid", "flagstone", "stone_plates", "stone_slabs", "stone:plates",
    "stone_blocks", "cobblestone:flattened", "interlock", "cement", "asphalt:lanes",
    "tartan", "rubber", "acrylic", "tiles", "plastic", "plastic_grate",
})
GRAVEL = frozenset({
    "gravel", "fine_gravel", "compacted", "pebblestone", "rock",
    # also present in the Swiss extract
    "grit", "stone", "stones", "rocks", "rocky", "bare_rock", "scree", "loose_fine_gravel",
})
DIRT = frozenset({
    "dirt", "earth", "ground", "mud", "sand", "grass", "clay", "woodchips", "unpaved",
    # also present in the Swiss extract
    "snow", "ice", "soil", "dirt/sand",
})

# Highway classes that are paved unless tagged otherwise. Footway, path, track
# and steps are deliberately absent: in Switzerland they are as often forest
# floor as they are asphalt, and guessing would hide that from the consumer.
ASSUMED_PAVED = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "residential",
    "living_street", "service", "unclassified", "cycleway", "pedestrian",
})

TRACKTYPE = {"grade1": "paved", "grade2": "gravel", "grade3": "gravel",
             "grade4": "dirt", "grade5": "dirt"}

STRUCTURE_LEVEL = {"bridge": 1, "tunnel": -1}


def surface_class(tags: Mapping[str, str]) -> tuple[str, str]:
    """Collapse ``surface=*`` to (paved | gravel | dirt | unknown, tagged | inferred | missing).

    A tagged value wins even when it is exotic ("unknown", "tagged"); only when
    the tag is absent do we infer from the highway class or ``tracktype``.
    """
    raw = tags.get("surface")
    if raw:
        # "asphalt;gravel" means the way changes surface; take the first, the
        # mapper's primary value.
        value = raw.split(";")[0].strip().lower()
        if value in PAVED:
            return "paved", "tagged"
        if value in GRAVEL:
            return "gravel", "tagged"
        if value in DIRT:
            return "dirt", "tagged"
        if value:
            return "unknown", "tagged"
    if tags.get("highway") in ASSUMED_PAVED:
        return "paved", "inferred"
    inferred = TRACKTYPE.get(tags.get("tracktype", ""))
    if inferred:
        return inferred, "inferred"
    return "unknown", "missing"


def _present(value: str | None) -> bool:
    return value is not None and value != "no"


def structure(tags: Mapping[str, str]) -> str:
    """``none | bridge | tunnel | gallery | ford``, first match wins in that order.

    ``tunnel=avalanche_protector`` is how Swiss avalanche galleries are tagged;
    they are open on one side, so they are reported as galleries, not tunnels.
    """
    if _present(tags.get("bridge")):
        return "bridge"
    tunnel = tags.get("tunnel")
    if _present(tunnel):
        return "gallery" if tunnel == "avalanche_protector" else "tunnel"
    if _present(tags.get("covered")):
        return "gallery"
    if _present(tags.get("ford")):
        return "ford"
    return "none"


def level(tags: Mapping[str, str], struct: str) -> int:
    """Vertical level: numeric ``layer=*`` if present, else +1 bridge / -1 tunnel / 0.

    Clamped to int8 so a stray ``layer=1000`` cannot break the schema.
    """
    raw = tags.get("layer")
    if raw is not None:
        try:
            return max(-128, min(127, int(raw.strip())))
        except ValueError:
            pass
    return STRUCTURE_LEVEL.get(struct, 0)
