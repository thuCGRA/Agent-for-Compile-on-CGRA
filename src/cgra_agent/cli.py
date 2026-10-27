"""Command line entry point.

    python -m cgra_agent.cli run --arm A --kernel array_add
    python -m cgra_agent.cli check
    python -m cgra_agent.cli facts --kernel array_add

The run is staged in work/armA/<kernel>/<stamp>/ and summarised on stdout.
Exit codes: 0 = fully verified, 1 = failed, 2 = mapped but not simulated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .pipeline import Pipeline, RunOutcome, load_config
from .tools.jobdir import ToolError

EXIT_OK, EXIT_FAIL, EXIT_UNVERIFIED = 0, 1, 2


def default_repo_root() -> str:
    env = os.environ.get("CGRA_REPO_ROOT")
    if env:
        return os.path.abspath(env)
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _summary(out: RunOutcome) -> str:
    r = out.run or {}
    res = r.get("result") or {}
    lines = []
    lines.append(
        "arm=%s kernel=%s status=%s II=%s lat=%s"
        % (r.get("arm"), r.get("kernel"), out.status,
           res.get("ii") or r.get("ii", {}).get("final"),
           res.get("lat") or "-")
    )
    if out.status == "ok":
        if res.get("sim") == "skipped(no trace)":
            lines.append("mapped, simulation skipped: no trace for this kernel")
        else:
            lines.append(
                "verified: Matches::%s Mismatches::%s cycles=%s"
                % (res.get("matches"), res.get("mismatches"), res.get("cycles"))
            )
    elif out.status == "unverified":
        lines.append("mapped but not verified: %s" % (r.get("error") or ""))
    else:
        lines.append("failed at %s: %s" % (r.get("stage"), (r.get("error") or "")[:400]))
    cdd = r.get("cdd") or {}
    if cdd:
        lines.append("diagnosis: %s (%s) -> %s" % (cdd.get("root_cause"), cdd.get("layer"), cdd.get("action")))
    llm = r.get("llm") or {}
    lines.append("llm calls: %s/%s via %s" % (llm.get("calls"), llm.get("max_calls"), llm.get("model")))
    lines.append("bundle: %s" % out.run_dir)
    return "\n".join(lines)


def cmd_run(args) -> int:
    pipe = Pipeline(
        repo_root=args.repo_root,
        kernel=args.kernel,
        arm=args.arm,
        seed=args.seed,
        ii=args.ii,
        out_root=args.out_root,
        max_llm_calls=args.max_llm_calls,
        mapper_timeout_s=args.mapper_timeout_s,
        kernel_walltime_s=args.walltime_s,
        trace=args.trace,
        pins_file=args.pins_file,
        placer=args.placer,
        router=args.router,
        pins_mode=args.pins_mode,
        pin_ratio=args.pin_ratio,
        pin_rule=args.pin_rule,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        verbose=not args.quiet,
    )
    try:
        out = pipe.run()
    except ToolError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    print("\n" + "-" * 72)
    print(_summary(out))
    if args.json:
        print(json.dumps(out.run, indent=2))
    if out.status == "ok":
        return EXIT_OK
    return EXIT_UNVERIFIED if out.status == "unverified" else EXIT_FAIL


def cmd_check(args) -> int:
    import requests

    base = (args.base_url or os.environ.get("CGRA_LLM_BASE_URL") or "http://127.0.0.1:18431/v1").rstrip("/")
    url = base + "/models"
    try:
        resp = requests.get(url, timeout=10)
        ok = resp.status_code == 200
        print("%s -> HTTP %s" % (url, resp.status_code))
        if ok:
            try:
                ids = [m.get("id") for m in resp.json().get("data", [])]
                print("models: %s" % ", ".join(str(i) for i in ids))
            except ValueError:
                pass
        return EXIT_OK if ok else EXIT_FAIL
    except Exception as exc:
        print("%s -> %s: %s" % (url, type(exc).__name__, exc))
        return EXIT_FAIL


def cmd_facts(args) -> int:
    from .tools.dfg import build_dfg
    from .tools.facts import build_facts, render_facts
    from .tools.jobdir import JobDir

    cfg = load_config(args.repo_root, args.kernel)
    try:
        dfg = build_dfg(
            JobDir(args.work),
            repo_root=args.repo_root,
            kernel=args.kernel,
            config=cfg,
        )
        facts = build_facts(dfg.dfg, dfg.arch, args.ii or cfg.get("init_ii") or 1)
    except ToolError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    out = os.path.join(args.work, "facts.json")
    with open(out, "w") as f:
        json.dump(facts, f, indent=2)
        f.write("\n")
    print(render_facts(facts))
    print("\nfacts written to %s" % out)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cgra_agent", description="Arm A: LLM placement + PathFinder routing")
    p.add_argument("--repo-root", default=default_repo_root())
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the S0-S9 pipeline")
    r.add_argument("--arm", default="A", choices=["A"])
    r.add_argument("--kernel", default="array_add")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--ii", type=int, default=None, help="run only this II (overrides the ladder)")
    r.add_argument("--out-root", default="work/armA")
    r.add_argument("--max-llm-calls", type=int, default=None)
    r.add_argument("--mapper-timeout-s", type=float, default=None)
    r.add_argument("--walltime-s", type=float, default=None)
    r.add_argument("--trace", default=None, help="a trace file or a directory of traces")
    r.add_argument("--pins-file", default=None, help="replay this pins.json instead of calling an LLM")
    r.add_argument(
        "--placer",
        default="llm",
        choices=["llm", "greedy", "fixed"],
        help="llm = arm A; greedy/fixed are evaluation references, not the arm itself",
    )
    r.add_argument(
        "--router",
        default="pathfinder",
        help="router slot (arm A: pathfinder = vendor pinned mapper; B arm registers its own)",
    )
    r.add_argument(
        "--pins-mode",
        type=int,
        default=None,
        choices=[1, 2, 3],
        help="force mapper pins mode (default: 1 when every node is pinned, else 3)",
    )
    r.add_argument(
        "--pin-ratio",
        type=float,
        default=1.0,
        help="pin density ablation: fraction of nodes to pin (0..1; <1 switches to repair mode)",
    )
    r.add_argument(
        "--pin-rule",
        default="degree",
        choices=["degree", "critical", "bank"],
        help="pin density ablation: which nodes to pin",
    )
    r.add_argument("--base-url", default=None)
    r.add_argument("--api-key", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--max-tokens", type=int, default=2048)
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--json", action="store_true", help="print the full run.json")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("check", help="check the OpenAI-compatible endpoint")
    c.add_argument("--base-url", default=None)
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("facts", help="build the DFG and dump facts.json")
    f.add_argument("--kernel", default="array_add")
    f.add_argument("--ii", type=int, default=None)
    f.add_argument("--work", default="work/facts")
    f.set_defaults(func=cmd_facts)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
