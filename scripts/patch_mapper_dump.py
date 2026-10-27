#!/usr/bin/env python3
"""Apply the mapper --dump-placement patch on an already-vendored tree.

toolchain/build.sh applies toolchain/patches/0004-mapper-dump.patch right after
0002-mapper.patch, so a normal build needs nothing here.  This script exists for
trees whose vendor was synced without re-running build.sh: it applies the same
patch -p1 with a marker file, exactly like build.sh does.
"""

import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAPPER = os.path.join(ROOT, "toolchain", "vendor", "mapper")
PATCH = os.path.join(ROOT, "toolchain", "patches", "0004-mapper-dump.patch")
MARKER = os.path.join(MAPPER, ".0004-mapper-dump.patch.applied")


def main():
    if not os.path.isfile(PATCH):
        print("missing", PATCH)
        return 2
    if os.path.isfile(MARKER):
        print("already applied:", PATCH)
        return 0
    with open(PATCH, "rb") as fh:
        proc = subprocess.run(["patch", "-p1", "-N", "--no-backup-if-mismatch"],
                              cwd=MAPPER, stdin=fh, capture_output=True, text=True)
    print(proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode != 0:
        return 1
    open(MARKER, "w").close()
    print("applied", PATCH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
