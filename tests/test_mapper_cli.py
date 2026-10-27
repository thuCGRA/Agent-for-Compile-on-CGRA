"""CLI contract tests for the vendored mapper's pin interface.

The mapper used to accept `--pins` without `--pins_mode` and then ignore the whole
pins file while still reporting ok=true.  That is the worst kind of failure: the
output looks perfectly normal.  It is now a hard error, and these tests keep it so.

Runs under pytest, or standalone (`python3 tests/test_mapper_cli.py`) because the
WSL conda env has no pytest installed.
"""

import json
import os
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPER = os.path.join(REPO, "toolchain", "out", "cgra_xml_mapper")
SPIKE = os.path.join(REPO, "work", "armB", "spike")


def _have_toolchain():
    return os.path.exists(MAPPER) and os.path.exists(os.path.join(SPIKE, "array_add_PartPredDFG.xml"))


def _base_args(pins=None, pins_mode=None):
    args = [MAPPER, "-m", "3",
            "-d", os.path.join(SPIKE, "array_add_PartPredDFG.xml"),
            "-x", "4", "-y", "4",
            "-j", os.path.join(SPIKE, "array_add_mem.json"),
            "-i", "4", "-t", "HyCUBE_4REG"]
    if pins:
        args += ["--pins", pins]
    if pins_mode is not None:
        args += ["--pins_mode", str(pins_mode)]
    return args


def _pins_file(tmpdir):
    path = os.path.join(tmpdir, "pins.json")
    with open(path, "w") as f:
        json.dump({"pins": [{"node": 0, "x": 1, "y": 1, "t": 3}]}, f)
    return path


def test_pins_without_mode_is_an_error(tmpdir):
    p = subprocess.run(_base_args(pins=_pins_file(tmpdir)), cwd=str(tmpdir),
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "would be ignored silently" in (p.stdout + p.stderr)


def test_invalid_pins_mode_is_an_error(tmpdir):
    p = subprocess.run(_base_args(pins=_pins_file(tmpdir), pins_mode=7), cwd=str(tmpdir),
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 2
    assert "--pins_mode takes" in (p.stdout + p.stderr)


def test_pins_with_mode_runs(tmpdir):
    p = subprocess.run(_base_args(pins=_pins_file(tmpdir), pins_mode=2), cwd=str(tmpdir),
                       capture_output=True, text=True, timeout=600)
    assert "would be ignored silently" not in (p.stdout + p.stderr)
    assert p.returncode in (0, 1)


def main():
    if not _have_toolchain():
        print("SKIP: mapper binary or spike fixture missing")
        return 0
    failures = 0
    for name, fn in ((n, f) for n, f in sorted(globals().items()) if n.startswith("test_")):
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(td)
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, exc)
    print("%d failed" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
