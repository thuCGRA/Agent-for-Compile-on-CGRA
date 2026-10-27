"""`over_permissive` 的钉子：`20->19` 那条已知能过 verify 的解。

背景：参考解里 `12->0` 在周期 3 到达而 SELECT 0 也在周期 3 执行，但那时只有 I1 有效，
仿真不报错。把 `20->19` 也注入后，两次运行的操作数写表逐行相同，仿真却在
`CGRATile.cpp:175 assert(!(I1isvalid && I2isvalid))` 崩溃——静态看不见"哪个周期
SELECT 真的执行"，只能看见到达周期。

于是这一格的三个裁判是：mapper ok / verify ok / sim abort，即 **verify 级漏报**。
"""

import glob
import json
import os
import shutil
import subprocess

REPO = "/home/steve/cac-armB"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
SPIKE = os.path.join(REPO, "work/armB/spike")
OUT = os.path.join(REPO, "work/armB/overperm")
DFG = "array_add_PartPredDFG.xml"
MEM = "array_add_mem.json"
ALLOC = "array_add_mem_alloc.txt"
TRACE = "array_add_trace_0.txt"
CASES = [("r19", "_rg_pins_r19.json", "_rg_routes_r19.json"),
         ("r19+sel", "_rg_pins_r19+sel.json", "_rg_routes_r19+sel.json")]


def run(wd, pins, routes, verify):
    for stale in glob.glob(os.path.join(wd, "*_binary.bin")):
        os.remove(stale)
    cmd = [MAPPER, "-m", "3", "-d", DFG, "-x", "4", "-y", "4", "-j", MEM, "-i", "4",
           "-t", "HyCUBE_4REG", "--pins", pins, "--routes", routes, "--pins_mode", "3",
           "--report", "report.json"]
    if verify:
        cmd.append("--verify")
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=600)
    open(os.path.join(wd, "mapper_%s.log" % ("verify" if verify else "plain")), "w").write(p.stdout + p.stderr)
    bins = glob.glob(os.path.join(wd, "*_binary.bin"))
    rep = {}
    try:
        rep = json.load(open(os.path.join(wd, "report.json")))
    except (OSError, ValueError):
        pass
    return {"rc": p.returncode, "ok": bool(rep.get("ok")), "bin": bins[0] if bins else "",
            "report": rep}


def sim(wd, binpath):
    p = subprocess.run([SIM, "-x", "4", "-y", "4", "-c", os.path.basename(binpath),
                        "-d", TRACE, "-a", ALLOC, "-m", "4096"],
                       cwd=wd, capture_output=True, text=True, timeout=1200)
    out = p.stdout + p.stderr
    open(os.path.join(wd, "sim.log"), "w").write(out)
    if "Mismatches::0" in out:
        return "pass", ""
    if "Assertion" in out:
        line = next((ln for ln in out.splitlines() if "Assertion" in ln), "")
        return "abort", line.strip()
    return out.strip().splitlines()[-1] if out.strip() else "no-output", ""


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for name, pins, routes in CASES:
        wd = os.path.join(OUT, name.replace("+", "_"))
        os.makedirs(wd, exist_ok=True)
        for f in (DFG, MEM, ALLOC, TRACE):
            shutil.copy2(os.path.join(SPIKE, f), os.path.join(wd, f))
        for f in (pins, routes):
            shutil.copy2(os.path.join(SPIKE, f), os.path.join(wd, f))
        plain = run(wd, pins, routes, verify=False)
        abst = os.path.join(wd, "A_bin.bin")
        if plain["bin"]:
            shutil.copy2(plain["bin"], abst)
        verified = run(wd, pins, routes, verify=True)
        verdict, evidence = sim(wd, abst) if abst else ("no-bin", "")
        rows.append({"case": name, "mapper_bin": bool(plain["bin"]),
                     "verify_ok": verified["ok"], "verify_conflicts": len(verified["report"].get("conflicts", [])),
                     "sim": verdict, "evidence": evidence})
        print("%-8s mapper_bin=%-5s verify_ok=%-5s conflicts=%-2d sim=%-6s %s" % (
            name, bool(plain["bin"]), verified["ok"],
            len(verified["report"].get("conflicts", [])), verdict, evidence[:70]), flush=True)
    with open(os.path.join(OUT, "overperm.json"), "w") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
