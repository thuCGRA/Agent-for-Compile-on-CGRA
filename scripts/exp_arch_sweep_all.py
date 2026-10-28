"""Full area/cycle sweep: every kernel at 4x4, 3x3 and 2x2.

Writes ``work/arch/sweep.csv`` incrementally so a long run can be inspected
while it is still going.  Each row is one (kernel, size) pair with the mapper's
II/latency and, when a bitstream and a trace exist, the simulated result.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from exp_arch_sweep import REPO, run  # noqa: E402

KERNELS = ["array_add", "array_cond", "trmm", "conv2d", "conv3",
           "mac", "mults1", "gemm", "gemm_pace", "hpcg"]
SIZES = [4, 3, 2]
OUT = os.path.join(REPO, "work/arch/sweep.csv")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for row in csv.DictReader(f):
                done.add((row["kernel"], int(row["size"])))
    header = ["kernel", "size", "result"]
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if not done:
            w.writerow(header)
        for kernel in KERNELS:
            for size in SIZES:
                if (kernel, size) in done:
                    continue
                res = run(kernel, size, size)
                w.writerow([kernel, size, res])
                f.flush()
                print("%-10s %dx%d  %s" % (kernel, size, size, res), flush=True)


if __name__ == "__main__":
    main()
