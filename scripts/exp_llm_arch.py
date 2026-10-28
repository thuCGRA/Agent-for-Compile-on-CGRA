"""Can the model choose an array size from the DFG facts alone?

The sweep says the interesting question is not "where to place" but "how big a
array does this kernel need".  That is a decision with a small, discrete answer,
a cheap verifier (simulate once) and a real cost model (PEs = size^2), so it is a
fair test of whether the model contributes anything at the architecture level.

Decision rule, fixed before the model is asked: pick the smallest size that
still passes functionally and stays within 10% of the 4x4 cycle count.  The
model never sees the cycle numbers -- it sees the facts a compiler would have.
"""
import csv
import json
import os
import re
import sys

REPO = "/home/steve/CGRA_Agent_Compiler"
sys.path.insert(0, os.path.join(REPO, "src"))
from cgra_agent.llm.client import LLMClient  # noqa: E402
from cgra_agent.tools.facts import build_facts, render_facts  # noqa: E402

SWEEP = os.path.join(REPO, "work/arch/sweep.csv")
JUDGEABLE = ["array_add", "array_cond", "trmm", "gemm"]

SYSTEM = """You are choosing the CGRA array size for one kernel.
Candidate sizes are 2x2 (4 PEs), 3x3 (9 PEs) and 4x4 (16 PEs).
Goal: the SMALLEST array that still maps, still produces functionally correct
results in cycle-accurate simulation, and whose cycle count stays within 10% of
what the same kernel gets on 4x4.
You cannot run the mapper or the simulator. You only have the DFG facts.
Key facts about this architecture: II is set by the DFG structure (RecMin), the
array only has to be big enough to host the nodes and their routing; a smaller
array forces a larger II once the node count approaches the PE count, and the
single memory-PE column limits how many loads/stores can be spread out.
Answer in exactly two lines, nothing else:
SIZE=<2|3|4>
WHY=<one sentence>"""


def load_sweep():
    out = {}
    with open(SWEEP) as f:
        for row in csv.DictReader(f):
            out[(row["kernel"], int(row["size"]))] = row["result"]
    return out


def parse_cycles(res):
    m = re.search(r"cycles=(\d+) (\d+)/(\d+)", res)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def truth(sweep, kernel):
    base = parse_cycles(sweep[(kernel, 4)])
    if not base:
        return None
    limit = base[0] * 1.10
    for size in (2, 3, 4):
        got = parse_cycles(sweep[(kernel, size)])
        if got and got[2] == 0 and got[0] <= limit:
            return size
    return 4


def main():
    sweep = load_sweep()
    client = LLMClient()
    rows = []
    for kernel in JUDGEABLE:
        kdir = os.path.join(REPO, "work/kernels", kernel)
        facts = build_facts(os.path.join(kdir, "dfg.xml"),
                            os.path.join(kdir, "mem.json"), 4)
        user = render_facts(facts) + "\n\nWhich array size for this kernel?"
        reply = client.chat([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ], max_tokens=4096, temperature=0.0)
        m = re.search(r"\{.*\}", reply, re.S)
        try:
            obj = json.loads(m.group(0)) if m else {}
        except ValueError:
            obj = {}
        size = obj.get("size")
        if not isinstance(size, int):
            # the endpoint often answers in prose ("a 4x4 grid"); take the first
            # NxN it names rather than scoring that as "no answer"
            mm = re.search(r"SIZE\s*=\s*([234])", reply)
            if mm:
                size = int(mm.group(1))
            else:
                mm = re.search(r"([234])\s*[x\u00d7]\s*\1", reply)
                size = int(mm.group(1)) if mm else None
        if not obj.get("why"):
            mw = re.search(r"WHY\s*=\s*(.+)", reply)
            if mw:
                obj["why"] = mw.group(1)
        want = truth(sweep, kernel)
        rows.append({"kernel": kernel, "llm": size, "truth": want,
                     "why": str(obj.get("why", ""))[:160]})
        print("%-10s llm=%s truth=%s  %s" % (kernel, size, want, rows[-1]["why"]), flush=True)
    with open(os.path.join(REPO, "work/arch/llm_arch.json"), "w") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
