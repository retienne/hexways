"""``hexways`` command line: ``build`` and ``stats``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .build import Options, build
from .sources import BBox, open_file, open_overpass
from .stats import compute, format_report


def _bbox(text: str) -> BBox:
    try:
        s, w, n, e = (float(x) for x in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("bbox must be S,W,N,E in decimal degrees") from None
    if not (s < n and w < e):
        raise argparse.ArgumentTypeError("bbox must satisfy S < N and W < E")
    return (s, w, n, e)


def _default_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hexways"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hexways", description=__doc__)
    p.add_argument("--version", action="version", version=f"hexways {__version__}")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="index the highway=* ways of an extract as H3 cells")
    src = b.add_argument_group("input (one of --pbf / --bbox; both to clip a file)")
    src.add_argument("--pbf", type=Path, metavar="FILE", help=".osm.pbf (or .osm XML) extract")
    src.add_argument("--bbox", type=_bbox, metavar="S,W,N,E",
                     help="fetch from Overpass, or restrict --pbf to ways touching the box")
    b.add_argument("--out", type=Path, required=True, metavar="DIR", help="output directory")
    b.add_argument("--resolution", type=int, default=13, help="H3 cell resolution (default 13)")
    b.add_argument("--corridor", type=int, default=1,
                   help="rings of cells around the line to include (default 1)")
    b.add_argument("--chunk-resolution", type=int, default=4,
                   help="one output file per H3 cell of this resolution (default 4)")
    b.add_argument("--workers", type=int, default=1,
                   help="worker processes for both phases (default 1)")
    b.add_argument("--cache-dir", type=Path, default=_default_cache(),
                   help="where Overpass responses are cached (default ~/.cache/hexways)")
    b.add_argument("--overwrite", action="store_true", help="replace a non-empty --out")
    b.add_argument("--no-stats", action="store_true", help="skip the report after building")

    s = sub.add_parser("stats", help="report cells, features per cell, structures and levels")
    s.add_argument("dir", type=Path, help="a directory written by `hexways build`")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    if args.command == "build":
        if args.pbf is None and args.bbox is None:
            print("hexways build: one of --pbf or --bbox is required", file=sys.stderr)
            return 2
        try:
            opts = Options(args.resolution, args.corridor, args.chunk_resolution, args.workers)
        except ValueError as exc:
            print(f"hexways build: {exc}", file=sys.stderr)
            return 2
        source = (open_file(args.pbf, args.bbox) if args.pbf
                  else open_overpass(args.bbox, args.cache_dir))
        try:
            build(source, args.out, opts, overwrite=args.overwrite)
        except FileExistsError as exc:
            print(f"hexways build: {exc}", file=sys.stderr)
            return 1
        if not args.no_stats:
            print(format_report(compute(args.out)))
        return 0
    if args.command == "stats":
        print(format_report(compute(args.dir)))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
