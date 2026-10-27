"""Is the mapper deterministic for a fixed pins+routes request?

The confusion matrix counts 27 cells where verify rejects and the simulator accepts.
That number is only meaningful if the two runs judged the same solution, so this
script re-runs every cell twice with identical inputs and compares:

  * the placement (node -> x,y,t,lat) of the report,
  * the routing (routeInfo log),
  * the bitstream sha256.

A cell whose two runs agree is deterministic; verify's rejection then really is a
rejection of the solution the simulator accepted.  A cell whose runs disagree is
`ambiguous`: neither a false kill nor a consistent pair, just undecided.
"""

import csv
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
OUT = "work/armB/confusion"
DET = "work/armB/determinism"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
II, XD, YD = 4, 4, 4


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run(wd, tag):
    for stale in glob.glob(os.path.join(wd, "*_binary.bin")):
        os.remove(stale)
    rep = "%s_report.json" % tag
    cmd = [MAPPER, "-m", "3", "-d", "dfg.xml", "-x", str(XD), "-y", str(YD),
           "-j", "mem.json", "-i", str(II), "-t", "HyCUBE_4REG",
           "--pins", "pins.json", "--routes", "routes.json",
           "--pins_mode", "2", "--report", rep]
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=900)
    data = {}
    try:
        data = json.load(open(os.path.join(wd, rep)))
    except (OSError, ValueError):
        pass
    binpath = None
    for f in glob.glob(os.path.join(wd, "*_binary.bin")):
        binpath = f
    roi = None
    for f in glob.glob(os.path.join(wd, "*.routeInfo.log")):
        roi = f
    return {
        "rc": p.returncode,
        "ok": bool(data.get("ok")),
        "placement": data.get("placement"),
        "ii": data.get("ii"),
        "lat": data.get("lat"),
        "bin": sha(binpath) if binpath else "",
        "routeinfo": open(roi).read() if roi else "",
    }


def main():
    os.makedirs(DET, exist_ok=True)
    rows = list(csv.DictReader(open(os.path.join(OUT, "confusion.csv"))))
    out = []
    for r in rows:
        if r["cell"] not in ("static_disagreement", "consistent_pass", "over_permissive"):
            continue
        src = os.path.join(OUT, r["kernel"], r["config"])
        wd = os.path.join(DET, r["kernel"], r["config"])
        os.makedirs(wd, exist_ok=True)
        for name in ("dfg.xml", "mem.json", "pins.json", "routes.json"):
            if os.path.exists(os.path.join(src, name)) and not os.path.exists(os.path.join(wd, name)):
                shutil.copy2(os.path.join(src, name), os.path.join(wd, name))
        a = run(wd, "det1")
        b = run(wd, "det2")
        same_place = a["placement"] == b["placement"]
        same_roi = a["routeinfo"] == b["routeinfo"]
        same_bin = a["bin"] == b["bin"] and a["bin"] != ""
        det = same_place and same_roi and same_bin
        if r["cell"] == "static_disagreement":
            if r["reject_kind"] in ("hint_route_unrealisable", "pin_infeasible_op"):
                verdict = "request_infeasible"
            elif det:
                verdict = "false_kill"
            else:
                verdict = "ambiguous"
        else:
            verdict = "consistent_pass" if det else "ambiguous"
        out.append({"kernel": r["kernel"], "config": r["config"], "cell": r["cell"],
                    "reject_kind": r["reject_kind"], "deterministic": det,
                    "same_placement": same_place, "same_routeinfo": same_roi,
                    "same_bin": same_bin, "verdict": verdict,
                    "bin1": a["bin"][:12], "bin2": b["bin"][:12]})
        print("%-10s %-5s %-20s det=%-5s %s" % (r["kernel"], r["config"], r["cell"], det, verdict),
              flush=True)
    with open(os.path.join(DET, "determinism.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print("=" * 30)
    print(Counter(r["verdict"] for r in out))
    print(Counter(r["cell"] for r in out))


if __name__ == "__main__":
    main()
