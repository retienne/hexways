import pytest

from hexways.tags import level, structure, surface_class


@pytest.mark.parametrize("value, expected", [
    ("asphalt", "paved"), ("paving_stones", "paved"), ("sett", "paved"), ("wood", "paved"),
    ("gravel", "gravel"), ("fine_gravel", "gravel"), ("compacted", "gravel"), ("rock", "gravel"),
    ("ground", "dirt"), ("grass", "dirt"), ("unpaved", "dirt"), ("mud", "dirt"),
])
def test_surface_tagged(value, expected):
    assert surface_class({"highway": "path", "surface": value}) == (expected, "tagged")


def test_surface_multiple_values_takes_first():
    assert surface_class({"surface": "asphalt;gravel"}) == ("paved", "tagged")
    assert surface_class({"surface": "gravel; grass"}) == ("gravel", "tagged")


def test_surface_case_and_whitespace():
    assert surface_class({"surface": " Asphalt "}) == ("paved", "tagged")


def test_surface_exotic_value_is_unknown_but_tagged():
    assert surface_class({"highway": "residential", "surface": "glass"}) == ("unknown", "tagged")


@pytest.mark.parametrize("highway", [
    "motorway", "trunk_link", "primary", "secondary", "tertiary_link", "residential",
    "living_street", "service", "unclassified", "cycleway", "pedestrian",
])
def test_surface_inferred_from_highway_class(highway):
    assert surface_class({"highway": highway}) == ("paved", "inferred")


@pytest.mark.parametrize("grade, expected", [
    ("grade1", "paved"), ("grade2", "gravel"), ("grade3", "gravel"),
    ("grade4", "dirt"), ("grade5", "dirt"),
])
def test_surface_inferred_from_tracktype(grade, expected):
    assert surface_class({"highway": "track", "tracktype": grade}) == (expected, "inferred")


def test_surface_tag_beats_inference():
    assert surface_class({"highway": "residential", "surface": "gravel"}) == ("gravel", "tagged")
    assert surface_class({"highway": "track", "tracktype": "grade1", "surface": "dirt"}) == (
        "dirt", "tagged")


@pytest.mark.parametrize("highway", ["path", "footway", "track", "steps", "bridleway"])
def test_surface_missing_for_unpaved_by_default_classes(highway):
    assert surface_class({"highway": highway}) == ("unknown", "missing")


@pytest.mark.parametrize("tags, expected", [
    ({}, "none"),
    ({"bridge": "yes"}, "bridge"),
    ({"bridge": "viaduct"}, "bridge"),
    ({"bridge": "no"}, "none"),
    ({"tunnel": "yes"}, "tunnel"),
    ({"tunnel": "building_passage"}, "tunnel"),
    ({"tunnel": "no"}, "none"),
    ({"tunnel": "avalanche_protector"}, "gallery"),
    ({"covered": "yes"}, "gallery"),
    ({"covered": "arcade"}, "gallery"),
    ({"covered": "no"}, "none"),
    ({"ford": "yes"}, "ford"),
    ({"ford": "stepping_stones"}, "ford"),
    ({"ford": "no"}, "none"),
])
def test_structure(tags, expected):
    assert structure(tags) == expected


def test_structure_precedence():
    assert structure({"bridge": "yes", "tunnel": "yes"}) == "bridge"
    assert structure({"tunnel": "yes", "covered": "yes"}) == "tunnel"
    assert structure({"covered": "yes", "ford": "yes"}) == "gallery"


@pytest.mark.parametrize("tags, struct, expected", [
    ({}, "none", 0),
    ({}, "bridge", 1),
    ({}, "tunnel", -1),
    ({}, "gallery", 0),
    ({}, "ford", 0),
    ({"layer": "2"}, "bridge", 2),
    ({"layer": "+1"}, "none", 1),
    ({"layer": "-2"}, "tunnel", -2),
    ({"layer": "0"}, "bridge", 0),        # an explicit layer wins over the structure default
    ({"layer": "0.1"}, "bridge", 1),      # unparsable: fall back to the structure default
    ({"layer": "1;2"}, "none", 0),
    ({"layer": "1000"}, "none", 127),     # clamped to int8
    ({"layer": "-1000"}, "none", -128),
])
def test_level(tags, struct, expected):
    assert level(tags, struct) == expected
