"""Do the router's own paths ever put two producers on one port?

The mapper rejected mac with "PORTPATH: edge 5 -> 20019 collides with an existing
route".  If this checker says the routes are clean, then the mapper's port graph
distinguishes ports my MRRG merged, and the bug is in mrrg.py rather than in the
flow model.
"""
import json
import sys

for k in sys.argv[1:] or ["array_add", "mac", "fir", "conv2", "trmm"]:
    try:
        d = json.load(open("/tmp/pb/%s/routes.json" % k))
    except (IOError, ValueError):
        print(k, "no routes.json")
        continue
    users = {}
    for r in d["routes"]:
        for (x, y, t, port) in r["path"]:
            users.setdefault((x, y, t, port), set()).add(r["src"])
    bad = {n: p for n, p in users.items() if len(p) > 1}
    print("%-10s edges=%-3d ports=%-4d multi-producer=%d"
          % (k, len(d["routes"]), len(users), len(bad)))
    for n, p in list(bad.items())[:10]:
        print("     ", n, "<-", sorted(p))
