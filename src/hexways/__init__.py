"""hexways — OpenStreetMap ways as H3 cells.

Turns the ``highway=*`` ways of an OpenStreetMap extract into a lookup table
keyed by H3 cell: one row per cell, carrying every transport feature that passes
within a corridor of it. See README.md for the output contract.
"""

__version__ = "0.1.0"
