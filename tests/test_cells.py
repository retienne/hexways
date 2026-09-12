import random

import h3
import numpy as np
import pytest

from hexways import cells


@pytest.fixture(scope="module")
def sample():
    rng = random.Random(7)
    lat_lon = [(rng.uniform(-80, 80), rng.uniform(-180, 180)) for _ in range(2000)]
    return {res: [h3.latlng_to_cell(la, lo, res) for la, lo in lat_lon] for res in (12, 13, 14)}


@pytest.mark.parametrize("res", [12, 13, 14])
@pytest.mark.parametrize("parent_res", [12, 4, 0])
def test_parent_matches_h3(sample, res, parent_res):
    if parent_res > res:
        pytest.skip("parent above child")
    ints = np.array([h3.str_to_int(c) for c in sample[res]], dtype=np.uint64)
    got = cells.parent(ints, res, parent_res)
    expected = [h3.cell_to_parent(c, parent_res) for c in sample[res]]
    assert [h3.int_to_str(int(x)) for x in got] == expected


def test_parent_rejects_impossible_resolution():
    with pytest.raises(ValueError):
        cells.parent(np.zeros(1, dtype=np.uint64), 12, 13)


@pytest.mark.parametrize("res", [12, 13, 14])
def test_to_strings_matches_h3(sample, res):
    ints = np.array([h3.str_to_int(c) for c in sample[res]], dtype=np.uint64)
    assert cells.to_strings(ints).to_pylist() == sample[res]


def test_to_strings_empty():
    assert cells.to_strings(np.zeros(0, dtype=np.uint64)).to_pylist() == []
