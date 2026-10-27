#!/usr/bin/env python3
"""Regenerate toolchain/patches/0004-mapper-dump.patch.

The vendor tree under toolchain/vendor/ is git-ignored, so the only durable
record of the --dump-placement feature is a patch in toolchain/patches/ that
build.sh applies next to 0002-mapper.patch.  This script derives the patch
from the current tree by removing the feature locally (in /tmp copies), so it
stays in sync even if the insertion points move.
"""
import os
import shutil
import subprocess
import sys

ROOT = "/home/steve/cac-armA"
SRC = os.path.join(ROOT, "toolchain/vendor/mapper")
CUR = "/tmp/mapper_cur"
CLEAN = "/tmp/mapper_clean"
OUT = os.path.join(ROOT, "toolchain/patches/0004-mapper-dump.patch")

FILES = ["include/morpher/util/util.h", "src/CGRA_xml_compiler.cpp"]


def strip_util(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    drop = {
        '\tstd::string dump_placement_file = ""; // optional: dump final placement (any method)',
        '        .add_option<std::string>("", "--dump-placement", "dump final placement json", "")',
        '\tret.dump_placement_file = args.get_option<std::string>("--dump-placement");',
    }
    out = [ln for ln in lines if ln not in drop]
    assert len(lines) - len(out) == 3, "util.h: expected 3 removed lines, got %d" % (len(lines) - len(out))
    open(path, "w", encoding="utf-8").write("\n".join(out))


def strip_compiler(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    start = None
    for i, ln in enumerate(lines):
        if "if (!args.dump_placement_file.empty())" in ln:
            start = i
            break
    assert start is not None, "dump block not found"
    depth = 0
    end = None
    for j in range(start, len(lines)):
        depth += lines[j].count("{") - lines[j].count("}")
        if depth == 0 and j > start:
            end = j
            break
    assert end is not None, "unbalanced dump block"
    del lines[start:end + 1]
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def diff(a, b, rel):
    proc = subprocess.run(
        ["diff", "-u", "--label", "a/" + rel, "--label", "b/" + rel, a, b],
        capture_output=True, text=True)
    return proc.stdout


def main():
    for p in (CUR, CLEAN):
        shutil.rmtree(p, ignore_errors=True)
        shutil.copytree(SRC, p)
    strip_util(os.path.join(CLEAN, FILES[0]))
    strip_compiler(os.path.join(CLEAN, FILES[1]))
    chunks = []
    for rel in FILES:
        chunks.append(diff(os.path.join(CLEAN, rel), os.path.join(CUR, rel), rel))
    text = "".join(chunks)
    assert "dump_placement" in text, "patch does not mention the feature"
    open(OUT, "w", encoding="utf-8").write(text)
    print("wrote", OUT, len(text), "bytes")

    # self-test: apply to a pristine copy and compare with the current tree
    test = "/tmp/mapper_test"
    shutil.rmtree(test, ignore_errors=True)
    shutil.copytree(CLEAN, test)
    proc = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "--batch",
                           "-i", OUT], cwd=test, capture_output=True, text=True)
    print(proc.stdout.strip()[-300:])
    assert proc.returncode == 0, proc.stderr
    same = subprocess.run(["diff", "-ru", "-x", "*.orig", test, CUR], capture_output=True, text=True)
    assert same.returncode == 0, "patched tree differs from current:\n" + same.stdout[:2000]
    print("patch applies cleanly and reproduces the current tree")


if __name__ == "__main__":
    sys.exit(main())
