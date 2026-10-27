#!/usr/bin/env python3
"""Cheap-predictor baseline: rule placement -> mapper (strict, then repair).

For each frozen kernel under work/kernels/<kernel>:
  facts = build_facts(dfg.xml, mem.json, II)
  placement = RulePlacer().place(facts, arch, II)     # no search, milliseconds
  mapper -m 3 --pins --pins_mode 1   (strict)
  mapper -m 3 --pins --pins_mode 3   (repair)

Writes work/eval-rule/rules.csv and prints the table the parent asked for:
strict_accept / lat / mapper time, next to the -m 0 baseline.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cgra_agent.interfaces import Placement  # noqa: E402
from cgra_agent.placers.rule import RuleError, RulePlacer  # noqa: E402
from cgra_agent.tools.facts import build_facts  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KROOT = os.path.join(REPO, "work", "kernels")
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
OUT = os.path.join(REPO, "work/eval-rule")
SUCCESS_RE = re.compile(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)")
ELAPSED_RE = re.compile(r"Elapsed time in seconds:\s*(\d+)\s*ms")

KERNELS = ["array_add", "array_cond", "trmm", "hpcg", "gemm", "gemm_pace",
           "conv3", "conv2d", "mac", "mults1"]
TARGET_II = {"gemm_pace": 9}


def baseline(kernel):
    """-m 0 II/lat from the master table (none mode)."""
    path = os.path.join(REPO, "work/master_table.csv")
    if not os.path.isfile(path):
        return None
    for row in csv.DictReader(open(path, encoding="utf-8")):
        if row["kernel"] == kernel and row["mode"] == "none":
            return row
    return None


def run_map(kernel, pins, mode, pins_mode):
    work = os.path.join(OUT, kernel, mode)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    for name in ("dfg.xml", "mem.json"):
        shutil.copy(os.path.join(KROOT, kernel, name), os.path.join(work, name))
    with open(os.path.join(work, "pins.json"), "w", encoding="utf-8") as fh:
        json.dump({"pins": [{"node": p["node"], "x": p["x"], "y": p["y"], "t": p["t"]}
                            for p in pins]}, fh)
    cmd = [MAPPER, "-d", "dfg.xml", "-x", "4", "-y", "4", "-j", "mem.json",
           "-i", str(TARGET_II.get(kernel, 4)), "-t", "HyCUBE_4REG", "-m", "3",
           "--pins", "pins.json", "--pins_mode", str(pins_mode), "--report", "rep.json"]
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout", "error": "mapper exceeded 90s",
                "ii": "", "lat": "", "applied": 0, "total": len(pins),
                "secs": round(time.time() - start, 2)}
    secs = time.time() - start
    report = {}
    if os.path.isfile(os.path.join(work, "rep.json")):
        try:
            report = json.load(open(os.path.join(work, "rep.json"), encoding="utf-8"))
        except Exception:
            report = {}
    m = SUCCESS_RE.search(proc.stdout)
    if m:
        report["ii"] = int(m.group(1))
        report["lat"] = int(m.group(2))
    ok = bool(m) or bool(report.get("ok"))
    return {"ok": ok, "ii": report.get("ii", ""), "lat": report.get("lat", ""),
            "stage": report.get("stage", ""), "error": report.get("error", ""),
            "applied": report.get("pins_applied", 0), "total": report.get("pins_total", len(pins)),
            "secs": round(secs, 2)}


def main():
    os.makedirs(OUT, exist_ok=True)
    placer = RulePlacer()
    rows = []
    for kernel in KERNELS:
        dfg = os.path.join(KROOT, kernel, "dfg.xml")
        arch_json = os.path.join(KROOT, kernel, "mem.json")
        if not os.path.isfile(dfg):
            print("skip", kernel, "(no frozen dfg)")
            continue
        facts = build_facts(dfg, arch_json, TARGET_II.get(kernel, 4))
        t0 = time.time()
        try:
            placement = placer.place(facts, facts, TARGET_II.get(kernel, 4), {})
            rule_secs = time.time() - t0
            pins = [asdict(p) for p in placement.pins]
            status = "placed"
        except RuleError as exc:
            rule_secs = time.time() - t0
            pins, status = [], "placement_not_found: %s" % exc
        strict = run_map(kernel, pins, "strict", 1) if pins else {"ok": False}
        repair = run_map(kernel, pins, "repair", 3) if pins else {"ok": False}
        base = baseline(kernel)
        rows.append({
            "kernel": kernel, "rule_status": status.split(":")[0], "rule_ms": round(rule_secs * 1000, 1),
            "strict_ok": strict["ok"], "strict_accept": "%s/%s" % (strict.get("applied"), strict.get("total")),
            "strict_ii": strict.get("ii", ""), "strict_lat": strict.get("lat", ""),
            "strict_secs": strict.get("secs", ""), "strict_stage": strict.get("stage", ""),
            "repair_ok": repair["ok"], "repair_accept": "%s/%s" % (repair.get("applied"), repair.get("total")),
            "repair_ii": repair.get("ii", ""), "repair_lat": repair.get("lat", ""),
            "repair_secs": repair.get("secs", ""),
            "none_ii": base["ii"] if base else "", "none_lat": base["lat"] if base else "",
            "none_secs": base["mapper_time_s"] if base else "",
            "lat_delta": (int(strict["lat"]) - int(base["lat"])) if (base and strict["ok"] and base["lat"]) else "",
        })
        print("%-10s rule=%-18s %5.1fms strict=%-5s accept=%-6s II=%-2s lat=%-3s %5.2fs | repair=%-5s %s/%s II=%s lat=%s | none=%s/%s" % (
            kernel, rows[-1]["rule_status"], rows[-1]["rule_ms"], strict["ok"],
            rows[-1]["strict_accept"], rows[-1]["strict_ii"], rows[-1]["strict_lat"],
            rows[-1]["strict_secs"] or 0, repair["ok"], repair.get("applied"), repair.get("total"),
            repair.get("ii", ""), repair.get("lat", ""), base["ii"] if base else "?", base["lat"] if base else "?"))

    csv_path = os.path.join(OUT, "rules.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote", csv_path)


if __name__ == "__main__":
    raise SystemExit(main())
