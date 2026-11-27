"""Replay the mapper's absolute-cycle reconstruction and look for collisions.

``--port-path`` stores phases (mod II); the mapper re-derives absolute cycles by
anchoring on the consumer and walking backwards, adding II when a step would run
before the producer.  Two edges can therefore share a port at the same *absolute*
cycle even though their phases differ, which is exactly what
"PORTPATH: edge 5 -> 20019 collides with an existing route" reports.  This script
does the same reconstruction the C++ does and reports the first offender with the
edge that already owns the port.
"""
import json
import sys

for k in sys.argv[1:] or ["mac"]:
    d = json.load(open("/tmp/pb/%s/routes.json" % k))
    ii = 4
    owners = {}
    for r in d["routes"]:
        path = r["path"]
        if not path:
            continue
        # phases relative to the first step; walk forward accumulating cycles
        lats = [path[0][2]]
        for a, b in zip(path, path[1:]):
            step = (b[2] - a[2]) % ii
            lats.append(lats[-1] + step)
        # anchor: the first step must not precede the producer's output cycle,
        # which we do not know here, so try offsets 0 and ii like the C++ does
        hit = None
        for bump in (0, ii):
            ls = [x + bump for x in lats]
            clash = None
            for (x, y, t, port), lat in zip(path, ls):
                key = (x, y, lat, port)
                own = owners.get(key)
                if own is not None and own != r["src"]:
                    clash = (key, own)
                    break
            if clash is None:
                for (x, y, t, port), lat in zip(path, ls):
                    owners[(x, y, lat, port)] = r["src"]
                hit = True
                break
        if hit is None:
            print("%s: edge %s -> %s has no clean absolute-cycle placement"
                  % (k, r["src"], r["dst"]))
            print("     path:", path)
            break
    else:
        bad = {}
        for key, own in owners.items():
            bad.setdefault(key, set()).add(own)
        multi = {n: p for n, p in bad.items() if len(p) > 1}
        print("%-8s edges=%d  ports(abs)=%d  multi-producer=%d"
              % (k, len(d["routes"]), len(owners), len(multi)))
