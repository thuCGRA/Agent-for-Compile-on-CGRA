#!/usr/bin/env python3
"""Extract a placement from a Morpher mapping run into the `--pins` format.

The mapper's only artifact that records the *phase* is `<...>.routeInfo.log`:
each mapped node starts a block

    node=11,mapped=PE_MEM_X0_Y2-T0
    node=12,mapped=PE_X1|_Y2|-T2

so the (x, y, t) triple is recoverable with a regex.  `*.mapping.csv` records
placement order only, and `*.mappingwithlatency.txt` is usually empty.

Usage:
    extract_pins.py <routeInfo.log> [-o pins.txt] [--t -1]
"""
import argparse
import os
import re
import sys

# PE_MEM_X0_Y2-T0   /   PE_X1|_Y2|-T2
NODE_RE = re.compile(r"^node=(\d+),mapped=(PE(?:_MEM)?)_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)\s*$")


def extract(path):
    pins = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = NODE_RE.match(line.strip())
            if not m:
                continue
            idx, kind, x, y, t = m.groups()
            pins[int(idx)] = (int(x), int(y), int(t), "mem" if kind == "PE_MEM" else "alu")
    return pins


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--t", type=int, default=None,
                    help="override phase: -1 writes a spatial-only pin")
    args = ap.parse_args(argv)

    pins = extract(args.log)
    if not pins:
        sys.stderr.write("no placement found in %s\n" % args.log)
        return 1

    lines = ["# node x y t   (t<0 = spatial only, phase left to the mapper)"]
    for idx in sorted(pins):
        x, y, t, _ = pins[idx]
        if args.t is not None:
            t = args.t
        lines.append("%d %d %d %d" % (idx, x, y, t))
    text = "\n".join(lines) + "\n"

    out = args.out or os.path.splitext(args.log)[0] + ".pins"
    with open(out, "w") as fh:
        fh.write(text)
    n_mem = sum(1 for v in pins.values() if v[3] == "mem")
    print("wrote %d pin(s) (%d mem PE) to %s" % (len(pins), n_mem, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
