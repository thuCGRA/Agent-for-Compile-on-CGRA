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
from .placers.fixed import FixedPlacer
from .placers.llm import LLMPlacer, PlacementSchemaError
from .tools.dfg import build_dfg
from .tools.facts import build_facts, facts_for_ii
from .tools.jobdir import Deadline, JobDir, ToolError, tail
from .tools.mapper import MapperOutcome, run_mapper
from .tools.sim import find_traces, run_sim
from .tools.validate import format_feedback, validate

MAX_SCHEMA_RETRIES = 2
MAX_VALIDATE_RETRIES = 2
MAX_MAP_RETRIES = 2
MAX_SIM_RETRIES = 1


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
        deadline = Deadline(walltime)

        # S0 init -----------------------------------------------------------
        run_dir = self._new_run_dir()
        job = JobDir(run_dir)
        cfg_snapshot = dict(cfg)
        cfg_snapshot.update(
            {
                "arm": self.arm,
                "seed": self.seed,
                "run_ii": self.ii_override or cfg.get("init_ii") or 1,
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
        if self.pins_file:
            placer = FixedPlacer.from_file(self.pins_file)
        else:
            client = LLMClient(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                call_log_path=llm_log,
            )
            placer = LLMPlacer(
                client,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                seed=self.seed,
            )

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

        ii = int(self.ii_override or cfg.get("init_ii") or 1)
        ii = max(1, ii)
        run["ii"] = {"start": ii, "max": max_ii, "final": None}
        attempts: List[Dict[str, Any]] = run["attempts"]
        feedback: Optional[str] = None
        previous: Optional[Any] = None
        schema_retries = validate_retries = map_retries = sim_retries = 0
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
            ii = floor
            facts = facts_for_ii(facts, ii)
            job.write_json("facts.json", facts)
            self._say("S2", "II raised to the resource bound %d" % ii)

        # S3..S8 loop -------------------------------------------------------
        run["stage"] = "S3"
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
            if ii > max_ii:
                error = "no feasible II up to max_ii=%d" % max_ii
                break

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
                schema_retries += 1
                attempts.append(
                    {"attempt": len(attempts) + 1, "ii": ii, "kind": "schema",
                     "error": str(exc)[:800]}
                )
                self._say("S3", "schema error #%d: %s" % (schema_retries, str(exc)[:160]))
                if schema_retries > MAX_SCHEMA_RETRIES:
                    error = "placer returned no valid placement json after %d re-asks" % MAX_SCHEMA_RETRIES
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

            llm_calls = len(client.records) if client is not None else llm_calls + 1
            previous = placement.to_dict()
            pins = [
                {"node": p.node, "x": p.x, "y": p.y, "t": p.t} for p in placement.pins
            ]
            complete = len({p["node"] for p in pins}) == facts["n_nodes"]
            pins_mode = 1 if complete else 3
            record: Dict[str, Any] = {
                "attempt": len(attempts) + 1,
                "kind": "placement",
                "ii": ii,
                "llm_call": llm_calls,
                "n_pins": len(pins),
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
                validate_retries += 1
                self._say(
                    "S4",
                    "rejected: %d error(s), e.g. %s"
                    % (len(val["errors"]), val["errors"][0][:140]),
                )
                if validate_retries <= MAX_VALIDATE_RETRIES:
                    feedback = format_feedback(val)
                    continue
                if ii < max_ii:
                    validate_retries = 0
                    ii += 1
                    facts = facts_for_ii(facts, ii)
                    job.write_json("facts.json", facts)
                    run["ii"]["final"] = ii
                    feedback = None
                    self._say("S4", "raising II to %d after repeated placement rejections" % ii)
                    continue
                error = "placement rejected by the validator after %d re-asks" % MAX_VALIDATE_RETRIES
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
            pins_path = map_job.write_json("pins.json", _pins_payload(placement, ii, placement.source, pins_mode))
            last_pins = job.read_json(pins_path)
            last_map_job = map_job
            try:
                out = run_mapper(
                    map_job,
                    repo_root=self.repo_root,
                    dfg=dfg.dfg,
                    arch=dfg.arch,
                    xdim=xdim,
                    ydim=ydim,
                    ii=ii,
                    pins=pins_path,
                    pins_mode=pins_mode,
                    timeout=deadline.clamp(mapper_timeout),
                )
            except ToolError as exc:
                error = "S5 mapper: %s" % exc
                break
            last_map = out
            record["map"] = _map_summary(out)
            if not out.report.ok:
                attempts.append(record)
                map_retries += 1
                self._say(
                    "S5",
                    "FAILED stage=%s pins %d/%d: %s"
                    % (out.report.stage, out.report.pins_applied, out.report.pins_total,
                       (out.report.error or "")[:160]),
                )
                if map_retries <= MAX_MAP_RETRIES:
                    if out.report.stage == "placement":
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
                if ii < max_ii:
                    ii += 1
                    map_retries = 0
                    validate_retries = 0
                    facts = facts_for_ii(facts, ii)
                    job.write_json("facts.json", facts)
                    run["ii"]["final"] = ii
                    feedback = (
                        "Routing kept failing at the previous II, so the agent must now place "
                        "the graph for II=%d: phases t in 0..%d." % (ii, ii - 1)
                    )
                    self._say("S5", "raising II to %d after mapper failures" % ii)
                    continue
                error = "mapper failed after %d re-placement(s)" % MAX_MAP_RETRIES
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
            traces = find_traces(self.repo_root, self.kernel, cfg, self.trace_arg)
            if not traces:
                run["result"] = {
                    "ii": out.report.ii,
                    "lat": out.report.lat,
                    "binary_sha256": out.binary_sha256,
                }
                status = "unverified"
                error = "mapping succeeded but no instrumentation trace exists for %s" % self.kernel
                attempts.append(record)
                self._say("S6", "SKIPPED: %s" % error)
                break
            sim_job = job.dir("sim-%02d" % mapper_rounds)
            sim_results = []
            last_log = ""
            for trace in traces[:4]:
                sim = run_sim(
                    sim_job,
                    repo_root=self.repo_root,
                    binary=out.binary,
                    trace=trace,
                    mem_alloc=dfg.mem_alloc,
                    xdim=xdim,
                    ydim=ydim,
                    timeout=deadline.clamp(120),
                    log_name="sim-%s.log" % os.path.basename(trace).replace(".txt", ""),
                )
                sim_results.append(sim.result)
                if sim.log:
                    last_log = sim.log
            sim_summary = _sim_summary(sim_results, traces)
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
                }
                self._say(
                    "S6",
                    "sim OK Matches=%d Mismatches=%d cycles=%d"
                    % (sim_summary["matches"], sim_summary["mismatches"], sim_summary["cycles"]),
                )
                break

            sim_retries += 1
            self._say("S6", "sim FAILED %s" % (sim_summary.get("evidence") or "")[:160])
            cdd = CDD(
                layer="node",
                root_cause="simulator mismatch after a legal placement: phase assignment is wrong",
                action="repin",
                evidence=[sim_summary.get("evidence") or ""],
                confidence=0.75,
            )
            if sim_retries <= MAX_SIM_RETRIES:
                feedback = (
                    "The mapping routed and the simulator ran, but %d address(es) held the "
                    "wrong value (%s). A mismatch is almost always a phase error: a value "
                    "arrives one or more cycles late/early. Keep the placement legal, but "
                    "change the phases so each parent->child edge has exactly the manhattan "
                    "distance as phase margin." % (sim_summary["mismatches"], sim_summary.get("evidence") or "")
                )
                continue
            error = "simulation mismatched %d address(es) after %d re-placement(s)" % (
                sim_summary["mismatches"], MAX_SIM_RETRIES)
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
