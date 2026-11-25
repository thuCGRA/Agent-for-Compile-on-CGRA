#!/usr/bin/env python3
"""Make the CP-SAT runs reproducible: honour CP_SAT_THREADS / CP_SAT_SEED.

Why this matters
----------------
`build_and_solve` used to hard-code `solver.parameters.num_search_workers = 8`.
With 8 workers CP-SAT returns a different (equally good) placement on every run,
so "does --routes help?" could not be answered: two runs of the same command gave
`pins_only=OK` once and `FAIL` the next time.  Pin the search to one worker (or a
fixed seed) before comparing anything.
"""
import io
import os
import re
import sys

P = "tools/ilp/ilp_mapper.py"


def main():
    src = io.open(P, encoding="utf-8").read()
    if "CP_SAT_THREADS" in src:
        print("already patched")
        return 0
    pat = re.compile(r"^(\s*)solver\.parameters\.num_search_workers\s*=\s*\d+\s*$", re.M)
    m = pat.search(src)
    if not m:
        print("ERROR: num_search_workers assignment not found", file=sys.stderr)
        return 1
    ind = m.group(1)
    repl = (
        "{ind}# CDETERM: reproducible runs (see tools/patches/patch_deterministic.py)\n"
        "{ind}solver.parameters.num_search_workers = int(os.environ.get(\"CP_SAT_THREADS\", \"8\"))\n"
        "{ind}solver.parameters.random_seed = int(os.environ.get(\"CP_SAT_SEED\", \"0\"))"
    ).format(ind=ind)
    src = src[: m.start()] + repl + src[m.end():]
    if not re.search(r"^import os$", src, re.M):
        src = re.sub(r"^(import )", "import os\n\\1", src, count=1, flags=re.M)
    io.open(P, "w", encoding="utf-8").write(src)
    print("patched", P)
    return 0


if __name__ == "__main__":
    sys.exit(main())