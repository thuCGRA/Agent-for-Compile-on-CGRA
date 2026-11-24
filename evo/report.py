"""Generate famou's HTML evolution report for one experiment.

famou ships a report generator (famou-v2/api_server/report.py) that turns an
experiment directory into an interactive dashboard: the score curve, the
per-island trajectory, a scatter of every program, and an LLM-written narrative
of how the search actually went.

It needs three things from us:
  * the experiment directory (famou_data/<exp>/ with programs/ and checkpoints),
  * an LLM endpoint (the narrative is generated, not templated), and
  * an output directory for data.json + report.html.

Usage (inside ~/acc-thu, famou env):

    python evo/report.py                       # newest experiment
    python evo/report.py --exp famou_data/<exp>
    python evo/report.py --out ~/acc-thu/out/report
"""
from __future__ import print_function

import argparse
import asyncio
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAMOU = os.path.join(os.path.expanduser("~"), "famou-v2")


def credentials():
    """Read the same endpoint the run.sh uses, so both agree."""
    path = os.path.join(os.path.expanduser("~"), ".config", "cgra_agent",
                        "credentials.env")
    env = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    base = env.get("CGRA_LLM_BASE_URL", "")
    if base and not base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return SimpleNamespace(api_key=env.get("CGRA_LLM_API_KEY", ""),
                           api_base=base,
                           model=env.get("CGRA_LLM_MODEL", ""))


def newest_experiment():
    base = os.path.join(ROOT, "famou_data")
    if not os.path.isdir(base):
        return None
    dirs = [os.path.join(base, d) for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))]
    dirs = [d for d in dirs if os.path.isdir(os.path.join(d, "programs"))]
    return max(dirs, key=os.path.getmtime) if dirs else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default=None, help="experiment directory")
    ap.add_argument("--out", default=None, help="output directory")
    args = ap.parse_args(argv)

    exp = args.exp or newest_experiment()
    if not exp or not os.path.isdir(exp):
        print("no experiment directory found under famou_data/")
        return 1
    out = args.out or os.path.join(ROOT, "out", "report")
    os.makedirs(out, exist_ok=True)

    cfg = credentials()
    if not cfg.api_key:
        print("missing CGRA_LLM_API_KEY in ~/.config/cgra_agent/credentials.env")
        return 1

    sys.path.insert(0, FAMOU)
    sys.path.insert(0, os.path.join(FAMOU, "api_server"))
    import report as famou_report                                   # noqa: E402

    print("experiment: %s" % exp)
    print("model     : %s" % cfg.model)
    print("output    : %s" % out)
    print("")
    print("generating (the narrative is LLM-written, so this takes a minute)...")

    asyncio.get_event_loop().run_until_complete(
        famou_report.build_report(exp, cfg, out))

    html = os.path.join(out, "report.html")
    data = os.path.join(out, "data.json")
    for p in (html, data):
        print("  %s  %s" % ("ok " if os.path.isfile(p) else "MISS", p))
    if os.path.isfile(html):
        print("")
        print("open it with: scp sutong_103:%s ." % html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
