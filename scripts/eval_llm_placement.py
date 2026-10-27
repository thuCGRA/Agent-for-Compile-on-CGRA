#!/usr/bin/env python3
"""LLM placement experiment: 10 kernels x II=4, one call + one self-repair.

Endpoint comes from CGRA_LLM_BASE_URL / CGRA_LLM_MODEL / CGRA_LLM_API_KEY.
Without CGRA_LLM_BASE_URL the script prints NO_ENDPOINT and exits 2: it never
invents a table.  Responses are cached under work/llm-cache keyed by
(kernel, dfg_sha, ii, prompt_sha), so reruns are reproducible and free.

Per kernel:
  call 1  -> Placement -> validate -> mapper strict (--pins_mode 1)
  if strict fails, call 2 with the mapper's raw error text fed back verbatim
  (only the affected nodes are supposed to move) -> mapper strict again
  finally mapper repair (--pins_mode 3) on the last placement.

Columns: strict_accept, repair_accept, lat, mapper_time, llm_calls,
prompt_tokens, completion_tokens; every raw response is written next to the CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from cgra_agent.interfaces import Placement  # noqa: E402
from cgra_agent.llm.cache import LLMCache, cache_key, prompt_sha, sha256_file  # noqa: E402
from cgra_agent.llm.client import LLMClient, LLMError  # noqa: E402
from cgra_agent.llm.prompts import build_messages  # noqa: E402
from cgra_agent.placers.llm import PlacementSchemaError, extract_json_object  # noqa: E402
from cgra_agent.tools.facts import build_facts  # noqa: E402
from cgra_agent.tools.jobdir import JobDir  # noqa: E402
from cgra_agent.tools.mapper import SUCCESS_RE  # noqa: E402

KROOT = os.path.join(ROOT, "work", "kernels")
MAPPER = os.path.join(ROOT, "toolchain/out/cgra_xml_mapper")
WORK = os.path.join(ROOT, "work", "eval-llm")
CACHE = os.path.join(ROOT, "work", "llm-cache")
KERNELS = ["array_add", "array_cond", "trmm", "hpcg", "gemm", "gemm_pace",
           "conv3", "conv2d", "mac", "mults1"]
ELAPSED_RE = re.compile(r"Elapsed time in seconds:\s*(\d+)\s*ms")
FAIL_RE = re.compile(r"(no candidate DP matches pin[^\n]*|Route Failed[^\n]*)")

SIM_RE = re.compile(r"Matches::(?P<m>\d+),\s*Mismatches::(?P<x>\d+)")
CYCLE_RE = re.compile(r"CGRA cycles:\s*(?P<c>\d+)")
SIM_BIN = os.path.join(ROOT, "toolchain", "out", "hycube_simulator")
TRACE_LIB = os.path.join(ROOT, "toolchain", "vendor", "simulator", "test", "library")


def find_binary(root):
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith("_binary.bin"):
                return os.path.join(base, name)
    return ""


def find_trace(kernel):
    kdir = os.path.join(KROOT, kernel)
    for name in sorted(os.listdir(kdir)) if os.path.isdir(kdir) else []:
        if name.startswith("trace") and name.endswith(".txt"):
            return os.path.join(kdir, name)
    cand = os.path.join(TRACE_LIB, kernel + "_trace_0.txt")
    if os.path.isfile(cand):
        return cand
    return ""


def sim_run(kernel, out_dir):
    """Simulate the mapping this run produced; empty dict when nothing to run."""
    binary = find_binary(out_dir) if os.path.isdir(out_dir) else ""
    trace = find_trace(kernel)
    mem_alloc = os.path.join(KROOT, kernel, "mem_alloc.txt")
    if not binary:
        return {"sim": "no-binary"}
    if not trace:
        return {"sim": "no-trace"}
    cmd = [SIM_BIN, "-x", "4", "-y", "4", "-c", binary, "-d", trace,
           "-a", mem_alloc, "-m", "4096"]
    try:
        out = subprocess.run(cmd, cwd=out_dir, capture_output=True, text=True, timeout=300).stdout
    except subprocess.TimeoutExpired:
        return {"sim": "timeout"}
    m = SIM_RE.search(out)
    cyc = CYCLE_RE.search(out)
    if not m:
        return {"sim": "abort", "mismatch": "", "cycles": cyc.group("c") if cyc else ""}
    mism = int(m.group("x"))
    return {"sim": "pass" if mism == 0 else "mismatch",
            "mismatch": mism, "cycles": cyc.group("c") if cyc else ""}


COLS = ["kernel", "strict_ok", "strict_accept", "strict_ii", "strict_lat",
        "strict_mapper_s", "repair_ok", "repair_accept", "repair_ii", "repair_lat",
        "repair_mapper_s", "llm_calls", "cached_calls", "prompt_tokens",
        "completion_tokens", "strict_error", "strict_sim", "strict_cycles", "repair_sim", "status"]


def mapper_run(kernel, pins, mode, pins_mode, ii):
    work = os.path.join(WORK, kernel, mode)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    for name in ("dfg.xml", "mem.json"):
        shutil.copy(os.path.join(KROOT, kernel, name), os.path.join(work, name))
    with open(os.path.join(work, "pins.json"), "w", encoding="utf-8") as fh:
        json.dump({"pins": pins}, fh)
    cmd = [MAPPER, "-d", "dfg.xml", "-x", "4", "-y", "4", "-j", "mem.json",
           "-i", str(ii), "-t", "HyCUBE_4REG", "-m", "3",
           "--pins", "pins.json", "--pins_mode", str(pins_mode),
           "--report", "rep.json"]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "ii": "", "lat": "", "error": "mapper timeout (120s)",
                "applied": 0, "total": len(pins), "secs": 120.0}
    secs = round(time.time() - started, 2)
    report = {}
    if os.path.isfile(os.path.join(work, "rep.json")):
        try:
            report = json.load(open(os.path.join(work, "rep.json"), encoding="utf-8"))
        except ValueError:
            report = {}
    m = SUCCESS_RE.search(proc.stdout)
    if m:
        report["ii"] = int(m.group(1))
        report["lat"] = int(m.group(2))
    ok = bool(m) or bool(report.get("ok"))
    err = ""
    if not ok:
        err = report.get("error") or ""
        if not err:
            f = FAIL_RE.search(proc.stdout + "\n" + proc.stderr)
            err = f.group(1) if f else (proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "mapper failed")
    return {"ok": ok, "ii": report.get("ii", ""), "lat": report.get("lat", ""),
            "error": err, "applied": report.get("pins_applied", 0),
            "total": report.get("pins_total", len(pins)), "secs": secs}


def parse_placement(text, ii):
    data = extract_json_object(text)
    if isinstance(data, str):
        data = json.loads(data)
    from cgra_agent.interfaces import Pin

    pins = []
    for entry in data.get("pins") or []:
        pins.append(Pin(node=int(entry["node"]), x=int(entry["x"]),
                        y=int(entry["y"]), t=int(entry.get("t", 0)) % ii))
    if not pins:
        raise PlacementSchemaError("no pins in response")
    return Placement(pins=pins, ii=ii, note=str(data.get("reasoning") or ""), source="llm")


def call_or_cache(client, cache, kernel, dfg_sha, ii, messages, out_dir, tag):
    key = cache_key(kernel, dfg_sha, ii, prompt_sha(messages))
    hit = cache.get(key)
    if hit is not None:
        return hit.get("response", ""), True, hit.get("usage") or {}
    text = client.chat(messages, temperature=0.0, max_tokens=4096)
    usage = (client.records[-1].get("usage") or {}) if client.records else {}
    cache.put(key, text, usage=usage)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, tag + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"messages": messages, "response": text, "usage": usage}, fh,
                  ensure_ascii=False, indent=1)
    return text, False, usage


def main(argv=None):
    global WORK
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=WORK)
    ap.add_argument("--cache-dir", default=CACHE)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--kernels", default="")
    args = ap.parse_args(argv)

    WORK = args.out
    base_url = (os.environ.get("CGRA_LLM_BASE_URL") or "").strip()
    if not base_url:
        print("NO_ENDPOINT")
        return 2
    if not os.path.isdir(KROOT):
        print("missing frozen kernels under work/kernels; run scripts/export_kernels.py")
        return 2

    try:
        client = LLMClient()
    except LLMError as exc:
        print("NO_ENDPOINT (%s)" % exc)
        return 2
    cache = LLMCache(args.cache_dir, enabled=not args.no_cache)

    kernels = [k.strip() for k in args.kernels.split(",") if k.strip()] or KERNELS
    rows = []
    for kernel in kernels:
        dfg = os.path.join(KROOT, kernel, "dfg.xml")
        arch = os.path.join(KROOT, kernel, "mem.json")
        if not os.path.isfile(dfg):
            print("skip", kernel)
            continue
        ii = 4
        dfg_sha = sha256_file(dfg)
        facts = build_facts(dfg, arch, ii)
        out_dir = os.path.join(WORK, kernel, "llm")
        shutil.rmtree(out_dir, ignore_errors=True)

        messages = build_messages(facts, ii)
        raw1, cached1, usage1 = call_or_cache(client, cache, kernel, dfg_sha, ii,
                                              messages, out_dir, "call1")
        llm_calls, cached_calls = 1, (1 if cached1 else 0)
        strict = repair = None
        error = ""
        try:
            placement = parse_placement(raw1, ii)
        except (PlacementSchemaError, ValueError, KeyError) as exc:
            placement = None
            error = "schema: %s" % exc

        if placement is not None:
            pins = [asdict(p) for p in placement.pins]
            strict = mapper_run(kernel, pins, "strict", 1, ii)
            if not strict["ok"]:
                error = strict["error"]
                messages2 = build_messages(facts, ii, feedback=error)
                raw2, cached2, usage2 = call_or_cache(client, cache, kernel, dfg_sha, ii,
                                                      messages2, out_dir, "call2")
                llm_calls += 1
                cached_calls += 1 if cached2 else 0
                usage1 = _add_usage(usage1, usage2)
                try:
                    placement2 = parse_placement(raw2, ii)
                    pins = [asdict(p) for p in placement2.pins]
                    strict = mapper_run(kernel, pins, "strict", 1, ii)
                    error = "" if strict["ok"] else strict["error"]
                except (PlacementSchemaError, ValueError, KeyError) as exc:
                    error = "schema after feedback: %s" % exc
            repair = mapper_run(kernel, pins, "repair", 3, ii)

        def acc(r):
            return "" if not r else "%s/%s" % (r.get("applied"), r.get("total"))

        s_sim = sim_run(kernel, os.path.join(WORK, kernel, "strict")) if (strict and strict["ok"]) else {}
        r_sim = sim_run(kernel, os.path.join(WORK, kernel, "repair")) if (repair and repair["ok"]) else {}

        rows.append({
            "kernel": kernel,
            "strict_ok": bool(strict and strict["ok"]),
            "strict_accept": acc(strict),
            "strict_ii": (strict or {}).get("ii", ""),
            "strict_lat": (strict or {}).get("lat", ""),
            "strict_mapper_s": (strict or {}).get("secs", ""),
            "repair_ok": bool(repair and repair["ok"]),
            "repair_accept": acc(repair),
            "repair_ii": (repair or {}).get("ii", ""),
            "repair_lat": (repair or {}).get("lat", ""),
            "repair_mapper_s": (repair or {}).get("secs", ""),
            "llm_calls": llm_calls,
            "cached_calls": cached_calls,
            "prompt_tokens": int(usage1.get("prompt_tokens") or 0),
            "completion_tokens": int(usage1.get("completion_tokens") or 0),
            "strict_error": error,
            "strict_sim": s_sim.get("sim", ""),
            "strict_cycles": s_sim.get("cycles", ""),
            "repair_sim": r_sim.get("sim", ""),
            "status": "ok" if (strict and strict["ok"]) else ("repair-only" if (repair and repair["ok"]) else "failed"),
        })
        print("%-10s strict=%-5s accept=%-7s II=%-2s lat=%-3s %6ss | repair=%-5s %s II=%s lat=%s | calls=%d cached=%d" % (
            kernel, rows[-1]["strict_ok"], rows[-1]["strict_accept"], rows[-1]["strict_ii"],
            rows[-1]["strict_lat"], rows[-1]["strict_mapper_s"], rows[-1]["repair_ok"],
            rows[-1]["repair_accept"], rows[-1]["repair_ii"], rows[-1]["repair_lat"],
            llm_calls, cached_calls))

    os.makedirs(WORK, exist_ok=True)
    out_csv = os.path.join(WORK, "llm.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print("wrote", out_csv, len(rows), "rows")
    return 0


def _add_usage(a, b):
    out = dict(a or {})
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if k in b:
            out[k] = int(out.get(k) or 0) + int(b.get(k) or 0)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
