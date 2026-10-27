#!/usr/bin/env python3
"""Dump the mapper's own placement and check it against our validator.

The mapper can tell us what *it* considers legal: ``--dump-placement`` writes
the final node -> (PE, cycle) mapping to JSON (patch in
``toolchain/vendor/mapper``; rebuilt into ``toolchain/out/cgra_xml_mapper``).
Feeding that mapping back through ``validate()`` separates two very different
stories when a heuristic placer cannot place a kernel:

* validator passes  -> the mapper found a legal layout our search missed
                       (search incompleteness, not a constraint problem);
* validator rejects -> our static rules are stricter than the mapper's real
                       constraints (the R3 lesson, second occurrence).

Usage:
    python scripts/diag_placement.py --dfg <dfg.xml> --arch <mem.json> --ii 4
        [--method 0] [--out work/diagnostics/<name>.json] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cgra_agent.interfaces import Pin  # noqa: E402
from cgra_agent.tools.facts import build_facts, parse_arch  # noqa: E402
from cgra_agent.tools.jobdir import JobDir, ToolError  # noqa: E402
from cgra_agent.tools.mapper import MAPPER_REL, find_binary  # noqa: E402
from cgra_agent.tools.validate import format_feedback, validate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dfg", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--ii", type=int, required=True)
    ap.add_argument("--method", type=int, default=0, help="mapper method (0 = vendor baseline)")
    ap.add_argument("--out", default="", help="where to keep the dumped placement json")
    ap.add_argument("--repo-root", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    dfg = os.path.abspath(args.dfg)
    arch = os.path.abspath(args.arch)
    facts = build_facts(dfg, arch, args.ii)
    archd = parse_arch(arch)
    exe = os.path.join(args.repo_root, MAPPER_REL)
    if not os.path.isfile(exe):
        print("no mapper at %s" % exe)
        return 1

    tmp = tempfile.mkdtemp(prefix="diag-")
    job = JobDir(tmp)
    dump_flag = os.path.join(job.path, "placement.json")
    cmd = [
        exe, "-x", str(facts["xdim"]), "-y", str(facts["ydim"]),
        "-d", dfg, "-j", arch, "-i", str(args.ii), "-t", "HyCUBE_4REG",
        "-m", str(args.method), "--dump-placement", dump_flag,
    ]
    run = job.run(cmd, timeout=args.timeout, log_name="mapper.log")
    if not os.path.isfile(dump_flag):
        print("mapper did not dump a placement (rc=%s); see %s" % (run.rc, run.log_path))
        print(run.tail(20))
        return 2
    dump = json.load(open(dump_flag))
    if not args.quiet:
        print("dump: ok=%s ii=%s method=%s nodes=%d"
              % (dump.get("ok"), dump.get("ii"), dump.get("mapping_method"), len(dump.get("placement") or [])))

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(dump, fh, indent=2)
        if not args.quiet:
            print("kept -> %s" % args.out)

    pins = [Pin(node=int(e["node"]), x=int(e["x"]), y=int(e["y"]), t=int(e["t"]))
            for e in dump.get("placement") or []]
    result = validate(facts, pins, args.ii, require_complete=True)
    print("validate: ok=%s errors=%d warnings=%d"
          % (result["ok"], len(result["errors"]), len(result["warnings"])))
    if result["errors"]:
        print(format_feedback(result, max_errors=50, max_warnings=5))
    elif not args.quiet:
        for w in result["warnings"][:6]:
            print("  WARN " + w)
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
