"""Try to repair an LLM placement that maps but aborts in simulation.

Diagnosis from the simulator dumps: the aborts at ``CGRATile.cpp:175`` are
SELECT/CMERGE nodes whose two operands land in the same cycle.  The LLM places
the two parents in adjacent phases "to be safe", but hop counts shift arrival
times, so adjacent phases are exactly the dangerous choice.

This experiment keeps the LLM's spatial assignment and only rewrites the phase
of mux parents so the two operands are two slots apart, then re-maps and
re-simulates.  If the kernel starts passing, the LLM's placement was usable and
only its timing model was wrong.
"""
import glob
import json
import os
import re
import subprocess
import sys

REPO = "/home/steve/CGRA_Agent_Compiler"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")


def dfg_parents(xml_path):
    txt = open(xml_path, errors="replace").read()
    ops, parents = {}, {}
    for m in re.finditer(r'<Node idx="(\d+)"(.*?)</Node>', txt, re.S):
        idx = int(m.group(1))
        body = m.group(2)
        op = re.search(r"<OP>([^<]*)</OP>", body)
        ops[idx] = op.group(1) if op else ""
        parents[idx] = [int(x) for x in re.findall(r'<Input idx="(\d+)"', body)]
    return ops, parents


def asap_of(xml_path):
    txt = open(xml_path, errors="replace").read()
    out = {}
    for m in re.finditer(r'<Node idx="(\d+)"[^>]*?ASAP="(-?\d+)"', txt):
        out[int(m.group(1))] = int(m.group(2))
    return out


def hybrid(kernel, pins, ii=4):
    """LLM keeps the spatial part, the phase comes from the DFG's ASAP."""
    asap = asap_of(os.path.join(REPO, "work/kernels", kernel, "dfg.xml"))
    out = []
    for p in pins["pins"]:
        q = dict(p)
        if int(q["node"]) in asap:
            q["t"] = asap[int(q["node"])] % ii
        out.append(q)
    return {"pins": out}


def load_pins(kernel):
    cands = []
    for root in ("work/eval-llm2", "work/eval-llm", "work/armA"):
        cands = sorted(glob.glob(os.path.join(REPO, root, kernel, "strict", "pins.json")))
        if not cands:
            cands = sorted(glob.glob(os.path.join(REPO, root, kernel, "*", "pins.json")))
        if cands:
            break
    if not cands:
        return None
    return json.load(open(cands[-1]))


def run(kernel, pins, tag):
    kdir = os.path.join(REPO, "work/kernels", kernel)
    dfg = os.path.join(kdir, "dfg.xml")
    arch = os.path.join(kdir, "arch.json")
    alloc = os.path.join(REPO, "work/traces", kernel, "%s_mem_alloc.txt" % kernel)
    trace = sorted(glob.glob(os.path.join(REPO, "work/traces", kernel, "*_trace_*.txt")))
    out = os.path.join(REPO, "work/selectfix", "%s-%s" % (kernel, tag))
    os.makedirs(out, exist_ok=True)
    pins_path = os.path.join(out, "pins.json")
    json.dump(pins, open(pins_path, "w"), indent=1)
    rep = os.path.join(out, "report.json")
    m = subprocess.run(
        [MAPPER, "-d", dfg, "-x", "4", "-y", "4", "-j", arch, "-i", "4",
         "-t", "HyCUBE_4REG", "-m", "3", "--pins", pins_path, "--pins_mode", "1",
         "--report", rep],
        cwd=kdir, capture_output=True, text=True, timeout=900)
    mm = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", m.stdout)
    iim = re.search(r"\bII\s*=\s*(\d+)", m.stdout)
    if not mm:
        return "map-fail (rc=%d) %s" % (m.returncode, (m.stdout or m.stderr).strip().splitlines()[-1:] )
    ii, lat = int(mm.group(1)), int(mm.group(2))
    bins = glob.glob(os.path.join(kdir, "*binary.bin"))
    if not bins:
        return "map ok II=%d lat=%d but no binary" % (ii, lat)
    binp = max(bins, key=os.path.getmtime)
    s = subprocess.run(
        [SIM, "-x", "4", "-y", "4", "-c", binp, "-d", trace[0], "-a", alloc, "-m", "4096"],
        cwd=out, capture_output=True, text=True, timeout=900)
    txt = (s.stdout or "") + (s.stderr or "")
    res = re.search(r"Matches::(\d+), Mismatches::(\d+)", txt)
    cyc = re.search(r"CGRA cycles: (\d+)", txt)
    if res:
        return "II=%d lat=%d  matches=%s mismatches=%s cycles=%s" % (
            ii, lat, res.group(1), res.group(2), cyc.group(1) if cyc else "?")
    if "Assertion" in txt:
        line = [l for l in txt.splitlines() if "Assertion" in l]
        return "II=%d lat=%d  ABORT %s" % (ii, lat, line[-1][:120] if line else "")
    return "II=%d lat=%d  sim unknown rc=%d" % (ii, lat, s.returncode)


def repair(kernel, pins, ii=4):
    ops, parents = dfg_parents(os.path.join(REPO, "work/kernels", kernel, "dfg.xml"))
    by_node = {int(p["node"]): dict(p) for p in pins["pins"]}
    changed = []
    for node, p in parents.items():
        if ops.get(node) not in ("SELECT", "CMERGE") or len(p) != 2:
            continue
        a, b = p
        if a not in by_node or b not in by_node:
            continue
        ta, tb = by_node[a]["t"], by_node[b]["t"]
        if (ta - tb) % ii == 2 or (tb - ta) % ii == 2:
            continue
        # 把较晚的那个父节点再推后一格，让两个操作数错开两拍
        victim = a if ta > tb else b
        by_node[victim]["t"] = (by_node[victim]["t"] + 1) % ii
        changed.append((node, victim, ta, tb, by_node[victim]["t"]))
    return {"pins": [by_node[k] for k in sorted(by_node)]}, changed


if __name__ == "__main__":
    for kernel in sys.argv[1:]:
        pins = load_pins(kernel)
        if pins is None:
            print("%-10s no llm pins" % kernel)
            continue
        fixed, changed = repair(kernel, json.loads(json.dumps(pins)))
        print("%-10s mux-parent shifts=%d %s" % (kernel, len(changed), changed[:3]))
        print("   before: %s" % run(kernel, pins, "before"))
        print("   after : %s" % run(kernel, fixed, "after"))
        print("   hybrid: %s" % run(kernel, hybrid(kernel, json.loads(json.dumps(pins))), "hybrid"))
