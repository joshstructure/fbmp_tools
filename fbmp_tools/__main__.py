"""Command line:  python -m fbmp_tools base.in base.out [--mirror X Y XY] [--types WS* WL* BR* WA* TU*] [--out DIR]"""
from __future__ import annotations

import argparse
import os

from . import DEFAULT_FAMILIES, DIRECTIONS, cases_for, mirror_loads, parse_in, read_model
from .mirror import format_report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fbmp_tools", description="Mirror FB-MultiPier directional loads.")
    ap.add_argument("infile", help="base .in file")
    ap.add_argument("outfile", help=".out file from a run of the base model")
    ap.add_argument("--mirror", nargs="+", default=list(DIRECTIONS), choices=DIRECTIONS)
    ap.add_argument("--types", nargs="+", default=list(DEFAULT_FAMILIES),
                    help="load types to mirror: families (WS) or patterns (WS*, BR3)")
    ap.add_argument("--out", default=None, help="output folder (default: next to the .in file)")
    ap.add_argument("--tolerance", type=float, default=24.0)
    args = ap.parse_args(argv)

    with open(args.infile, "rb") as fh:
        infile = parse_in(fh.read())
    model = read_model(args.outfile)
    selected = cases_for(infile, args.types)
    folder = args.out or os.path.dirname(os.path.abspath(args.infile))
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.infile))[0]
    for direction in args.mirror:
        res = mirror_loads(infile, model, direction, selected, tolerance=args.tolerance)
        path = os.path.join(folder, f"{stem}_Mirror{direction}")
        with open(path + ".in", "w", encoding="latin-1", newline="") as fh:
            fh.write(res.text)
        with open(path + "_report.txt", "w", encoding="utf-8") as fh:
            fh.write(format_report(res))
        print(format_report(res))
        print(f"wrote {path}.in\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
