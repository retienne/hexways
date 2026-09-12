"""Vectorised H3 index arithmetic on numpy arrays of 64-bit cell indexes.

h3-py only offers scalar calls for what we need here (parent, to-string), and
fifty million scalar calls are minutes we would rather not spend. Both
functions are checked against h3-py on random cells in the tests.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa

_RES_SHIFT = np.uint64(52)
_RES_MASK = ~np.uint64(0xF << 52)
_HEX = np.frombuffer(b"0123456789abcdef", dtype=np.uint8)


def parent(cells: np.ndarray, res: int, parent_res: int) -> np.ndarray:
    """``h3.cell_to_parent`` for an array of cells that are all at ``res``.

    The H3 index stores one 3-bit digit per resolution; the digits below the
    parent's resolution are set to 7 ("unused") and the resolution field is
    rewritten.
    """
    if not 0 <= parent_res <= res:
        raise ValueError(f"parent resolution {parent_res} above cell resolution {res}")
    unused = np.uint64(((1 << (3 * (res - parent_res))) - 1) << (3 * (15 - res)))
    return ((cells | unused) & _RES_MASK) | (np.uint64(parent_res) << _RES_SHIFT)


def to_strings(cells: np.ndarray) -> pa.Array:
    """``h3.int_to_str`` for an array: 15 lowercase hex digits per cell."""
    n = len(cells)
    chars = np.empty((n, 15), dtype=np.uint8)
    for pos in range(15):
        shift = np.uint64(4 * (14 - pos))
        chars[:, pos] = _HEX[((cells >> shift) & np.uint64(0xF)).astype(np.uint8)]
    offsets = np.arange(0, 15 * (n + 1), 15, dtype=np.int32)
    return pa.StringArray.from_buffers(n, pa.py_buffer(offsets), pa.py_buffer(chars.reshape(-1)))
