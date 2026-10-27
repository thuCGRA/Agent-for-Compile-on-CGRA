"""Arm A pipeline: S0..S9 with the LLM as the only placer.

The state machine follows docs/WORKFLOW.md.  The interesting part is the
failure routing: a schema error, a validator rejection and a mapper rejection
are three different signals and each goes back to the placer with concrete
evidence, while simulator mismatches are attributed by ``diagnose`` before the
last re-ask.  Every branch is bounded, so a bad endpoint cannot burn the wall
clock or the call budget.

    dfg -> facts -> place -> validate -> map -> sim -> accept
                     ^          |         |       |
                     +----------+---------+-------+  feedback (bounded)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .interfaces import CDD, Placement
from .llm.client import LLMClient, LLMError
from .placers import make_placer
from .placers.greedy import GreedyError
from .placers.llm import PlacementSchemaError
from .routers import DEFAULT_ROUTER, make_router
from .tools.dfg import build_dfg
from .tools.facts import build_facts, facts_for_ii
from .tools.jobdir import Deadline, JobDir, ToolError, tail
from .tools.mapper import MapperOutcome
from .tools.sim import run_sim
from .tools.trace import TraceNotFound, resolve_trace
from .tools.validate import format_feedback, validate

# One budget, split into per-failure-type buckets (the rest is slack).  A
# bucket that runs dry abandons that path and moves to the next II; buckets
# never borrow from each other, so a bad placement cannot eat the routing
# budget.
BUCKET_LIMITS = {"validate": 2, "placement": 3, "route": 3, "sim": 1}
DEFAULT_LADDER = (4, 5, 6, 8)


@dataclass
class RunOutcome:
    ok: bool
    status: str
    run_dir: str
    run: Dict[str, Any]

    @property
    def ii(self) -> int:
        return int((self.run.get("result") or {}).get("ii") or 0)

    @property
    def lat(self) -> int:
        return int((self.run.get("result") or {}).get("lat") or 0)


def load_config(repo_root: str, kernel: str) -> Dict[str, Any]:
    path = os.path.join(repo_root, "configs", kernel + ".yaml")
    if not os.path.isfile(path):
        raise ToolError("no config for kernel %s (expected %s)" % (kernel, path))
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ToolError("config %s is not a mapping" % path)
    return cfg


def _ii_ladder(cfg, override, max_ii: int) -> List[int]:
    """The fixed II ladder: all arms must search the same values in the same order."""
    if override is not None:
        raw = [int(override)]
    elif cfg.get("ii_ladder"):
        raw = [int(v) for v in cfg["ii_ladder"]]
    else:
        start = max(1, int(cfg.get("init_ii") or DEFAULT_LADDER[0]))
        raw = [start] + [v for v in DEFAULT_LADDER if v > start]
    ladder: List[int] = []
    for v in raw:
        v = max(1, int(v))
        if v > max_ii or v in ladder:
            continue
        ladder.append(v)
    return ladder or [max(1, min(max_ii, DEFAULT_LADDER[0]))]


def _pins_payload(placement: Placement, ii: int, source: str, mode: int) -> Dict[str, Any]:
    return {
        "pins": [
            {"node": p.node, "x": p.x, "y": p.y, "t": p.t} for p in placement.pins
        ],
        "ii": int(ii),
        "source": source,
        "reasoning": placement.note,
        "pins_mode": int(mode),
    }


def _map_summary(out: MapperOutcome) -> Dict[str, Any]:
    mp = out.report
    return {
        "ok": mp.ok,
        "stage": mp.stage,
        "ii": mp.ii,
        "lat": mp.lat,
        "pins_total": mp.pins_total,
        "pins_applied": mp.pins_applied,
        "pins_rejected": mp.pins_rejected,
        "error": mp.error,
        "elapsed_s": mp.elapsed_s,
        "binary": os.path.basename(out.binary) if out.binary else "",
        "binary_sha256": out.binary_sha256,
        "placement": mp.placement,
    }


def _sim_summary(results: List[Any], traces: List[str]) -> Dict[str, Any]:
    worst = None
    for r in results:
        if worst is None or (r.mismatches, -r.matches) > (worst.mismatches, -worst.matches):
            worst = r
    if worst is None:
        return {"ok": False, "matches": 0, "mismatches": 0, "cycles": 0,
                "evidence": "no trace available", "traces": 0}
    return {
        "ok": all(r.ok for r in results) and bool(results),
        "matches": sum(r.matches for r in results),
        "mismatches": max(r.mismatches for r in results),
        "cycles": max(r.cycles for r in results),
        "evidence": worst.evidence,
        "traces": len(results),
        "trace_files": [os.path.basename(t) for t in traces[: len(results)]],
    }


class Pipeline:
    def __init__(
        self,
        *,
        repo_root: str,
        kernel: str,
        arm: str = "A",
        seed: int = 0,
        ii: Optional[int] = None,
        out_root: str = "work/armA",
        max_llm_calls: Optional[int] = None,
        mapper_timeout_s: Optional[float] = None,
        kernel_walltime_s: Optional[float] = None,
        trace: Optional[str] = None,
        pins_file: Optional[str] = None,
        placer: str = "llm",
        router: str = DEFAULT_ROUTER,
        pins_mode: Optional[int] = None,
        pin_ratio: float = 1.0,
        pin_rule: str = "degree",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        verbose: bool = True,
    ):
        self.repo_root = os.path.abspath(repo_root)
        self.kernel = kernel
        self.arm = arm.upper()
        self.seed = int(seed)
        self.ii_override = ii
        self.out_root = out_root
        self.max_llm_calls_override = max_llm_calls
        self.mapper_timeout_override = mapper_timeout_s
        self.walltime_override = kernel_walltime_s
        self.trace_arg = trace
        self.pins_file = pins_file
        self.placer_kind = (placer or "llm").lower()
        self.router_kind = (router or DEFAULT_ROUTER).lower()
        self.pins_mode = pins_mode
        self.pin_ratio = float(pin_ratio)
        self.pin_rule = (pin_rule or "degree").lower()
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.verbose = verbose
        self.lines: List[str] = []

    # ------------------------------------------------------------------ log
    def _say(self, stage: str, msg: str) -> None:
        line = "[%s] %s" % (stage, msg)
        self.lines.append(line)
        if self.verbose:
            print(line, flush=True)

    # ------------------------------------------------------------------ run
    def run(self) -> RunOutcome:
        started = time.monotonic()
        cfg = load_config(self.repo_root, self.kernel)
        if self.arm != "A" and not self.pins_file:
            raise ToolError("this pipeline implements arm A only (got %s)" % self.arm)

        xdim = int(cfg.get("xdim") or 4)
        ydim = int(cfg.get("ydim") or 4)
        max_ii = int(cfg.get("max_ii") or 8)
        mapper_timeout = float(
            self.mapper_timeout_override or cfg.get("mapper_timeout_s") or 600
        )
        walltime = float(self.walltime_override or cfg.get("kernel_walltime_s") or 1200)
        max_llm_calls = int(
            self.max_llm_calls_override or cfg.get("max_llm_calls") or 12
        )
        max_rounds = int(cfg.get("max_rounds") or 4)
        ladder = _ii_ladder(cfg, self.ii_override, max_ii)
        deadline = Deadline(walltime)
        router = make_router(
            self.router_kind, repo_root=self.repo_root, xdim=xdim, ydim=ydim, timeout=mapper_timeout
        )

        # S0 init -----------------------------------------------------------
        run_dir = self._new_run_dir()
        job = JobDir(run_dir)
        cfg_snapshot = dict(cfg)
        cfg_snapshot.update(
            {
                "arm": self.arm,
                "seed": self.seed,
                "run_ii": ladder[0],
                "ii_ladder": ladder,
                "max_llm_calls": max_llm_calls,
                "mapper_timeout_s": mapper_timeout,
                "kernel_walltime_s": walltime,
            }
        )
        job.write_text("config.yaml", yaml.safe_dump(cfg_snapshot, sort_keys=False))
        self._say("S0", "init kernel=%s arm=%s seed=%d run=%s"
                  % (self.kernel, self.arm, self.seed, os.path.relpath(run_dir, self.repo_root)))

        llm_log = job.file("llm_calls.jsonl")
        client = None
        if self.placer_kind == "llm":
            client = LLMClient(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                call_log_path=llm_log,
            )
        try:
            placer = make_placer(
                self.placer_kind,
                client=client,
                pins_file=self.pins_file,
                seed=self.seed,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        except ValueError as exc:
            raise ToolError(str(exc))

        run: Dict[str, Any] = {
            "arm": self.arm,
            "kernel": self.kernel,
            "seed": self.seed,
            "status": "failed",
            "stage": "S0",
            "run_dir": os.path.abspath(run_dir),
            "config": cfg_snapshot,
            "dfg": {},
            "facts": {},
            "ii": {},
            "attempts": [],
            "result": {},
            "llm": {},
            "artifacts": {},
            "timings": {},
            "cdd": None,
            "error": "",
        }

        ii = int(ladder[0])
        run["ii"] = {"start": ladder[0], "ladder": ladder, "max": max_ii, "final": None}
        attempts: List[Dict[str, Any]] = run["attempts"]
        feedback: Optional[str] = None
        previous: Optional[Any] = None
        buckets = {"validate": 0, "placement": 0, "route": 0, "sim": 0}
        mapper_rounds = 0
        llm_calls = 0
        status = "failed"
        error = ""
        cdd: Optional[CDD] = None
        last_pins: Optional[Dict[str, Any]] = None
        last_map: Optional[MapperOutcome] = None
        last_map_job: Optional[JobDir] = None
        last_sim: Dict[str, Any] = {}
        last_sim_log = ""

        # S1 dfg ------------------------------------------------------------
        run["stage"] = "S1"
        dfg_job = job.dir("dfg")
        try:
            dfg = build_dfg(
                dfg_job,
                repo_root=self.repo_root,
                kernel=self.kernel,
                config=cfg,
                timeout=deadline.clamp(300),
            )
        except ToolError as exc:
            error = "S1 dfg: %s" % exc
            self._say("S1", "FAILED %s" % exc)
            return self._finish(job, run, started, status, error, cdd)
        run["dfg"] = {
            "dfg": os.path.relpath(dfg.dfg, run_dir),
            "mem_alloc": os.path.relpath(dfg.mem_alloc, run_dir),
            "arch": os.path.relpath(dfg.arch, run_dir),
            "node_count": dfg.node_count,
            "sha256": dfg.sha256,
            "secs": dfg.secs,
        }
        self._say(
            "S1",
            "dfg %d nodes (%.1fs) %s"
            % (dfg.node_count, dfg.secs, os.path.basename(dfg.dfg)),
        )

        # S2 facts ----------------------------------------------------------
        run["stage"] = "S2"
        try:
            facts = build_facts(dfg.dfg, dfg.arch, ii)
        except Exception as exc:
            error = "S2 facts: %s" % exc
            self._say("S2", "FAILED %s" % exc)
            return self._finish(job, run, started, status, error, cdd)
        job.write_json("facts.json", facts)
        run["facts"] = {
            "res_min_ii": facts["res_min_ii"],
            "n_nodes": facts["n_nodes"],
            "n_edges": facts["n_edges"],
            "pe_mem": len(facts["pe_mem"]),
            "phase_capacity": facts["phase_capacity"],
            "arch_warnings": facts["arch_warnings"],
        }
        self._say(
            "S2",
            "facts nodes=%d edges=%d ResMII=%d min_ii=%d"
            % (facts["n_nodes"], facts["n_edges"], facts["res_min_ii"], facts["min_ii"]),
        )
        if not facts["pe_mem"]:
            error = "S2 facts: architecture declares no memory PE"
            return self._finish(job, run, started, status, error, cdd)
        floor = max(1, int(facts["min_ii"]))
        if ii < floor:
            start_pos = next((i for i, v in enumerate(ladder) if v >= floor), None)
            if start_pos is None:
                error = "S2 facts: min_ii=%d exceeds the II ladder %s" % (floor, ladder)
                return self._finish(job, run, started, status, error, cdd)
            ii = int(ladder[start_pos])
            facts = facts_for_ii(facts, ii)
            job.write_json("facts.json", facts)
            self._say("S2", "II raised to the resource bound %d" % ii)
        else:
            start_pos = 0

        # S3..S8 loop -------------------------------------------------------
        run["stage"] = "S3"
        ladder_pos = start_pos

        def advance_ii() -> bool:
            """Move to the next II in the ladder, resetting every bucket."""
            nonlocal ladder_pos
            ladder_pos += 1
            if ladder_pos >= len(ladder):
                return False
            for key in buckets:
                buckets[key] = 0
            return True

        while True:
            if deadline.expired():
                error = "kernel wall clock exceeded after %.0fs" % deadline.elapsed()
                break
            if llm_calls >= max_llm_calls:
                error = "LLM call budget exhausted (%d calls)" % llm_calls
                break
            if mapper_rounds >= max_rounds:
                error = "mapper round budget exhausted (%d rounds)" % mapper_rounds
                break
            if ladder_pos >= len(ladder):
                error = "no feasible II in ladder %s" % ladder
                break
            ii = int(ladder[ladder_pos])
            if ii != int(facts.get("ii") or 0):
                facts = facts_for_ii(facts, ii)
                job.write_json("facts.json", facts)

            # --- S3 place -------------------------------------------------
            run["stage"] = "S3"
            try:
                placement = placer.place(
                    facts,
                    facts,
                    ii,
                    {"max_llm_calls": max_llm_calls,
                     "remaining_s": deadline.remaining(),
                     "deadline": deadline},
                    feedback=feedback,
                    previous=previous,
                )
            except PlacementSchemaError as exc:
                if client is not None:
                    llm_calls = len(client.records)
                else:
                    llm_calls += 1
                buckets["placement"] += 1
                attempts.append(
                    {"attempt": len(attempts) + 1, "ii": ii, "kind": "schema",
                     "error": str(exc)[:800]}
                )
                self._say("S3", "schema error #%d: %s"
                          % (buckets["placement"], str(exc)[:160]))
                if buckets["placement"] > BUCKET_LIMITS["placement"]:
                    if advance_ii():
                        feedback = None
                        continue
                    error = ("placer returned no valid placement json after %d attempt(s)"
                             % buckets["placement"])
                    cdd = CDD(layer="node", root_cause=str(exc)[:300], action="abort",
                              evidence=[str(exc)[:300]], confidence=0.9)
                    break
                feedback = (
                    "The answer could not be parsed as the placement contract: %s. "
                    "Answer with exactly one json object: "
                    '{"pins":[{"node":int,"x":int,"y":int,"t":int}],"reasoning":str,"ii":int}'
                    % str(exc)[:400]
                )
                continue
            except LLMError as exc:
                error = "S3 llm: %s" % exc
                self._say("S3", "FAILED %s" % exc)
                break
            except GreedyError as exc:
                buckets["placement"] += 1
                attempts.append(
                    {"attempt": len(attempts) + 1, "ii": ii, "kind": "greedy",
                     "error": str(exc)[:400]}
                )
                self._say("S3", "greedy placement failed at II=%d: %s" % (ii, str(exc)[:160]))
                if advance_ii():
                    feedback = None
                    continue
                error = "greedy placer could not place the graph: %s" % exc
                break

            llm_calls = len(client.records) if client is not None else llm_calls + 1
            previous = placement.to_dict()
            pins = [
                {"node": p.node, "x": p.x, "y": p.y, "t": p.t} for p in placement.pins
            ]
            complete = len({p["node"] for p in pins}) == facts["n_nodes"]
            if self.pin_ratio < 1.0:
                # 密度消融：只保留选点规则挑中的节点，其余交给 mapper 自己布。
                from .placers.pin_select import RULES, select as select_pins

                if self.pin_rule not in RULES:
                    raise ToolError(
                        "unknown pin rule %r (expected one of %s)"
                        % (self.pin_rule, ", ".join(RULES))
                    )
                keep = set(select_pins(facts, self.pin_ratio, self.pin_rule))
                pins = [p for p in pins if p["node"] in keep]
                complete = False
            pins_mode = self.pins_mode if self.pins_mode is not None else (1 if complete else 3)
            record: Dict[str, Any] = {
                "attempt": len(attempts) + 1,
                "kind": "placement",
                "ii": ii,
                "llm_call": llm_calls,
                "n_pins": len(pins),
                "pin_ratio": self.pin_ratio,
                "pin_rule": self.pin_rule,
                "pins_mode": pins_mode,
                "reasoning": placement.note[:400],
            }
            self._say(
                "S3",
                "place #%d: %d pin(s) mode=%s (llm call %d/%d)"
                % (record["attempt"], len(pins), pins_mode, llm_calls, max_llm_calls),
            )

            # --- S4 validate ---------------------------------------------
            run["stage"] = "S4"
            val = validate(facts, pins, ii, require_complete=complete)
            record["validate"] = {
                "ok": val["ok"],
                "errors": val["errors"][:12],
                "warnings": val["warnings"][:8],
                "stats": val["stats"],
            }
            if val["errors"]:
                attempts.append(record)
                buckets["validate"] += 1
                self._say(
                    "S4",
                    "rejected: %d error(s), e.g. %s"
                    % (len(val["errors"]), val["errors"][0][:140]),
                )
                if buckets["validate"] <= BUCKET_LIMITS["validate"]:
                    feedback = format_feedback(val)
                    continue
                if advance_ii():
                    feedback = None
                    self._say("S4", "II raised to %d after repeated placement rejections"
                              % ladder[ladder_pos])
                    continue
                error = "placement rejected by the validator after %d re-asks" % buckets["validate"]
                cdd = CDD(layer="node", root_cause=val["errors"][0][:300],
                          action="repin", evidence=val["errors"][:5], confidence=0.8)
                break
            self._say(
                "S4",
                "validate ok (%d warning(s), %d/%d slots)"
                % (len(val["warnings"]), val["stats"]["slots_used"], val["stats"]["slots_total"]),
            )

            # --- S5 map ---------------------------------------------------
            run["stage"] = "S5"
            mapper_rounds += 1
            map_job = job.dir("map-%02d" % mapper_rounds)
            pins_path = map_job.write_json(
                "pins.json",
                {
                    "pins": pins,
                    "reasoning": (placement.note or "")[:800],
                    "ii": ii,
                    "source": placement.source,
                    "pin_ratio": self.pin_ratio,
                    "pin_rule": self.pin_rule,
                },
            )
            last_pins = job.read_json(pins_path)
            last_map_job = map_job
            try:
                router.timeout = deadline.clamp(mapper_timeout)
                out = router.route(
                    job=map_job,
                    dfg=dfg.dfg,
                    arch=dfg.arch,
                    ii=ii,
                    pins=pins_path,
                    pins_mode=pins_mode,
                )
            except ToolError as exc:
                error = "S5 mapper: %s" % exc
                break
            last_map = out
            record["map"] = _map_summary(out)
            if not out.report.ok:
                attempts.append(record)
                bucket = "placement" if out.report.stage == "placement" else "route"
                buckets[bucket] += 1
                self._say(
                    "S5",
                    "FAILED stage=%s pins %d/%d: %s"
                    % (out.report.stage, out.report.pins_applied, out.report.pins_total,
                       (out.report.error or "")[:160]),
                )
                if buckets[bucket] <= BUCKET_LIMITS[bucket]:
                    if bucket == "placement":
                        feedback = (
                            "The mapper rejected the placement at its placement stage: %s\n"
                            "Pins are applied one node at a time in scheduler order; a pin on a "
                            "(PE, phase) slot that an earlier node already took is dropped and "
                            "counts as rejected. Keep every (x,y,t) distinct and leave the "
                            "memory PEs at x=0 for LOAD/STORE." % (out.report.error or "")[:500]
                        )
                    else:
                        feedback = (
                            "The mapper accepted every pin but failed to route at II=%d: %s\n"
                            "Routing moves one hop per cycle: shorten long edges, keep parents "
                            "and children within 2 columns, and make the phase difference equal "
                            "to the manhattan distance." % (ii, (out.report.error or "")[:500])
                        )
                    continue
                if advance_ii():
                    feedback = (
                        "Routing kept failing at II=%d, so the agent must now place "
                        "the graph for II=%d: phases t in 0..%d."
                        % (ii, ladder[ladder_pos], ladder[ladder_pos] - 1)
                    )
                    self._say("S5", "II raised to %d after mapper failures" % ladder[ladder_pos])
                    continue
                error = "mapper failed after %d re-placement(s)" % buckets[bucket]
                cdd = CDD(layer="edge", root_cause=(out.report.error or "")[:300],
                          action="reroute", evidence=[out.report.error or ""], confidence=0.7)
                break
            self._say(
                "S5",
                "map ok II=%d lat=%d pins %d/%d (%.1fs)"
                % (out.report.ii, out.report.lat, out.report.pins_applied,
                   out.report.pins_total, out.report.elapsed_s),
            )

            # --- S6 sim ---------------------------------------------------
            run["stage"] = "S6"
            try:
                trace_path, trace_tier = resolve_trace(
                    self.repo_root, self.kernel, cfg, self.trace_arg)
            except TraceNotFound as exc:
                sim_summary = {"skipped": "no trace", "tier": "none",
                               "evidence": str(exc)[:600]}
                last_sim = sim_summary
                record["sim"] = sim_summary
                attempts.append(record)
                run["result"] = {
                    "ii": out.report.ii,
                    "lat": out.report.lat,
                    "binary_sha256": out.binary_sha256,
                    "sim": "skipped(no trace)",
                }
                status = "ok"
                self._say("S6", "SKIPPED (no trace): %s" % str(exc)[:140])
                break
            sim_job = job.dir("sim-%02d" % mapper_rounds)
            sim = run_sim(
                sim_job,
                repo_root=self.repo_root,
                binary=out.binary,
                trace=trace_path,
                mem_alloc=dfg.mem_alloc,
                xdim=xdim,
                ydim=ydim,
                timeout=deadline.clamp(120),
                log_name="sim-%s.log" % os.path.basename(trace_path).replace(".txt", ""),
            )
            sim_results = [sim.result]
            last_log = sim.log or ""
            sim_summary = _sim_summary(sim_results, [trace_path])
            sim_summary["trace"] = os.path.basename(trace_path)
            sim_summary["tier"] = trace_tier
            last_sim = sim_summary
            last_sim_log = last_log
            record["sim"] = sim_summary
            attempts.append(record)
            if sim_summary["ok"]:
                status = "ok"
                run["result"] = {
                    "ii": out.report.ii,
                    "lat": out.report.lat,
                    "matches": sim_summary["matches"],
                    "mismatches": sim_summary["mismatches"],
                    "cycles": sim_summary["cycles"],
                    "binary_sha256": out.binary_sha256,
                    "sim": "verified",
                }
                self._say(
                    "S6",
                    "sim OK Matches=%d Mismatches=%d cycles=%d"
                    % (sim_summary["matches"], sim_summary["mismatches"], sim_summary["cycles"]),
                )
                break

            buckets["sim"] += 1
            self._say("S6", "sim FAILED %s" % (sim_summary.get("evidence") or "")[:160])
            cdd = CDD(
                layer="node",
                root_cause="simulator mismatch after a legal placement: phase assignment is wrong",
                action="repin",
                evidence=[sim_summary.get("evidence") or ""],
                confidence=0.75,
            )
            if buckets["sim"] <= BUCKET_LIMITS["sim"]:
                feedback = (
                    "The mapping routed and the simulator ran, but %d address(es) held the "
                    "wrong value (%s). A mismatch is almost always a phase error: a value "
                    "arrives one or more cycles late/early. Keep the placement legal, but "
                    "change the phases so each parent->child edge has exactly the manhattan "
                    "distance as phase margin." % (sim_summary["mismatches"], sim_summary.get("evidence") or "")
                )
                continue
            if advance_ii():
                feedback = "Placement for II=%d (phases 0..%d) after a simulator mismatch." % (
                    ladder[ladder_pos], ladder[ladder_pos] - 1)
                continue
            error = "simulation mismatched %d address(es) after %d re-placement(s)" % (
                sim_summary["mismatches"], buckets["sim"])
            break

        # S9 report ---------------------------------------------------------
        run["stage"] = "S9"
        run["ii"]["final"] = ii if run["ii"].get("final") is None else run["ii"]["final"]
        final_ii = int(run["ii"]["final"] or ii)
        if last_pins is not None:
            job.write_json("pins.json", last_pins)
        if last_map is not None and last_map.report_json:
            job.write_json("report.json", last_map.report_json)
        elif last_map is not None:
            job.write_json(
                "report.json",
                {
                    "ok": last_map.report.ok,
                    "ii": last_map.report.ii,
                    "stage": last_map.report.stage,
                    "pins_total": last_map.report.pins_total,
                    "pins_applied": last_map.report.pins_applied,
                    "pin_attempts": last_map.report.pins_applied,
                    "pin_attempts_note": "cumulative across mapper internal passes; not distinct nodes",
                    "pins_rejected": last_map.report.pins_rejected,
                    "error": last_map.report.error,
                    "placement": last_map.report.placement,
                },
            )
        if last_sim:
            body = last_sim_log or ""
            job.write_text(
                "sim.txt",
                "# summary: %s\n%s"
                % (json.dumps(last_sim, sort_keys=True), body[-20000:]),
            )
        if last_map_job is not None and last_map is not None:
            for name, key in (("map_report.json", "report"),):
                src = last_map_job.file(name)
                if os.path.isfile(src):
                    job.copy_in(src, key + ".json")
            for name in os.listdir(last_map_job.path):
                if name.endswith("_binary.bin"):
                    job.copy_in(last_map_job.file(name), "bitstream.bin")
                    break

        art = {}
        for name in ("config.yaml", "facts.json", "pins.json", "report.json", "sim.txt", "bitstream.bin"):
            p = job.file(name)
            if os.path.isfile(p):
                art[name] = {"sha256": JobDir.sha256(p), "bytes": os.path.getsize(p)}
        run["artifacts"] = art
        run["llm"] = {
            "base_url": client.base_url if client else "",
            "model": client.model if client else "fixed",
            "calls": llm_calls,
            "max_calls": max_llm_calls,
            "records": [
                {k: v for k, v in rec.items() if k in
                 ("n", "model", "secs", "attempts", "ok", "error", "usage")}
                for rec in (client.records if client else [])
            ],
            "calls_log": os.path.basename(llm_log) if client else "",
        }
        run["pins"] = {
            "ratio": self.pin_ratio,
            "rule": self.pin_rule,
            "applied": last_map.report.pins_applied if last_map else 0,
            "total": last_map.report.pins_total if last_map else 0,
            # mapper 的 pins_applied 是“内部各轮放置尝试累计成功次数”，不是去重节点数，
            # 可能大于 pins_total；这里同时给出原始值和语义说明，避免误读。
            "applied_semantics": "cumulative pin applications across mapper's internal passes",
        }
        run["timings"] = {
            "total_s": round(time.monotonic() - started, 2),
            "dfg_s": dfg.secs,
        }
        run["cdd"] = cdd.to_dict() if hasattr(cdd, "to_dict") else (
            cdd.__dict__ if cdd is not None else None
        )
        return self._finish(job, run, started, status, error, cdd, final_ii=final_ii)

    # ------------------------------------------------------------------ misc
    def _new_run_dir(self) -> str:
        root = os.path.join(self.repo_root, self.out_root, self.kernel)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(root, stamp)
        n = 1
        while os.path.exists(path):
            path = os.path.join(root, "%s-%d" % (stamp, n))
            n += 1
        return path

    def _finish(
        self,
        job: JobDir,
        run: Dict[str, Any],
        started: float,
        status: str,
        error: str,
        cdd: Optional[CDD],
        *,
        final_ii: Optional[int] = None,
    ) -> RunOutcome:
        run["status"] = status
        run["error"] = error or ""
        if cdd is not None:
            run["cdd"] = cdd.__dict__
        run["timings"]["total_s"] = round(time.monotonic() - started, 2)
        if final_ii is not None:
            run["ii"]["final"] = int(final_ii)
        if status == "ok":
            self._say("S9", "bundle %s (II=%s, status=ok)" % (job.path, run["result"].get("ii")))
        elif status == "unverified":
            self._say("S9", "bundle %s (mapped, unverified: %s)" % (job.path, error))
        else:
            self._say("S9", "bundle %s (failed at %s: %s)" % (job.path, run["stage"], error))
        job.write_json("run.json", run)
        job.write_text("run.log", "\n".join(self.lines) + "\n")
        return RunOutcome(
            ok=(status == "ok"),
            status=status,
            run_dir=str(job.path),
            run=run,
        )
