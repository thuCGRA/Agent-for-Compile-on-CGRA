"""agent 主循环（LangGraph StateGraph）。

     plan ──► execute ──► (还有预算且未停) ──► plan
                  └────► END（ok / 预算用完 / 策略喊停）

三个节点之间只通过一个 typed state 通信：
    plan    调策略，拿一个动作
    execute 真跑一次尝试（后端 + 四门），把结果记进 history/experience
    （归因在 execute 里顺手做，因为它要读四门的 detail）

设计上的两条硬规矩：
1. **判据只有仿真器**：`state["best"]` 只在 G1 通过时更新。
2. **预算是硬上限**：`used >= budget` 一定停，不管策略还想不想试。
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, TypedDict

from agents import audit as audit_mod
from agents.engine import load_problem, run_attempt, summarize
from experience.store import Store


class AgentState(TypedDict, total=False):
    kernel: str
    ii: int
    budget: int
    used: int
    facts_text: str
    backend: str
    params: Dict[str, Any]
    pins: Dict[int, List[int]]
    forbidden: List[List[int]]
    pending: Dict[str, Any]
    history: List[dict]
    tried: List[str]
    audit: dict
    best: dict
    done: bool
    stop_reason: str
    store: Any
    root: Any
    do_sim: bool
    log: List[str]


def facts_text_of(prob: dict) -> str:
    ops: Dict[str, int] = {}
    for n in prob["nodes"]:
        ops[n["op"]] = ops.get(n["op"], 0) + 1
    top = sorted(ops.items(), key=lambda kv: -kv[1])[:8]
    deg = {}
    for e in prob["edges"]:
        deg[e[0]] = deg.get(e[0], 0) + 1
    hottest = sorted(deg.items(), key=lambda kv: -kv[1])[:5]
    return ("kernel=%s  nodes=%d  edges=%d  array=%dx%d\n"
            "op 直方图(前8): %s\n出度最大的节点: %s\n"
            "资源下界 res_min_ii=%s（II 低于 RecMinII 时 mapper 会自动抬回，低报无效）"
            % (prob["kernel"], prob["n_nodes"], len(prob["edges"]),
               prob["xdim"], prob["ydim"],
               ", ".join("%s×%d" % kv for kv in top),
               ", ".join("#%d(%d)" % kv for kv in hottest),
               prob.get("res_min_ii", "?")))


def _better(att, best: dict) -> bool:
    """只有 G1 过了才配叫"解"；同 cost 时比 lat（SA 常把 cost 追平但延迟更差）。"""
    if not getattr(att, "ok", False):
        return False
    if not best:
        return True
    c, bc = (att.cost or 0.0), (best.get("cost") or 0.0)
    if abs(c - bc) > 1e-9:
        return c < bc
    return (att.lat or 0) < (best.get("lat") or 0)


def _key(act: dict, state: dict) -> str:
    import json
    ii = state["ii"] + act.get("ii_delta", 0)
    return json.dumps({"b": act.get("backend"), "ii": ii,
                       "p": act.get("params") or {},
                       "pins": sorted((act.get("pins") or {}).items())},
                      sort_keys=True, ensure_ascii=False)


def build_graph(policy, store: Store, stop_on_first_ok: bool = False):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:               # pragma: no cover
        return None

    def plan(state: AgentState) -> dict:
        if state.get("used", 0) >= state.get("budget", 0):
            return {"done": True, "stop_reason": "预算用完"}
        act = policy.suggest(dict(state))
        log = list(state.get("log") or []) + ["plan: %s" % act.get("action")]
        if act.get("action") == "stop":
            return {"done": True, "stop_reason": act.get("why", "策略喊停"),
                    "pending": act, "log": log}
        return {"pending": act, "done": False, "log": log}

    def execute(state: AgentState) -> dict:
        act = state.get("pending") or {}
        backend = state.get("backend", "pathfinder")
        params = dict(state.get("params") or {})
        ii = state["ii"]
        pins = dict(state.get("pins") or {})
        forbidden = list(state.get("forbidden") or [])

        if act.get("action") == "set_backend":
            backend = act["backend"]
            params = dict(act.get("params") or {})
            ii = state["ii"] + act.get("ii_delta", 0)
        elif act.get("action") == "constrain":
            pins = {int(k): list(v) for k, v in (act.get("pins") or {}).items()}
            forbidden = [list(x) for x in (act.get("forbidden") or [])]

        prob = load_problem(state["kernel"], ii, state.get("root"))
        att = run_attempt(state["kernel"], ii, backend, params=params,
                          pins=pins, forbidden=forbidden, root=state.get("root"),
                          do_sim=state.get("do_sim", True), prob=prob)
        summ = summarize(att)
        dia = audit_mod.audit(att, prob)

        history = list(state.get("history") or []) + [summ]
        tried = list(state.get("tried") or [])
        if act.get("action") in ("set_backend", "constrain"):
            tried.append(_key(act, state))

        best = dict(state.get("best") or {})
        if _better(att, best):
            best = dict(summ)

        st = state.get("store")
        if st is not None:
            st.record(kernel=state["kernel"], n_nodes=prob["n_nodes"],
                      n_edges=len(prob["edges"]), ii=att.ii, backend=backend,
                      params=params, ok=att.ok, cost=att.cost, lat=att.lat,
                      gate=dia.get("gate", ""), cls=dia.get("cls", ""),
                      note=(dia.get("reason") or "")[:200])

        return {"backend": backend, "params": params, "ii": ii, "pins": pins,
                "forbidden": forbidden, "history": history, "tried": tried,
                "audit": dia, "best": best, "used": state.get("used", 0) + 1,
                "done": False,
                "log": list(state.get("log") or []) + ["execute: %s/%s ii=%d ok=%s"
                                                       % (backend, params, ii, att.ok)]}

    def after_execute(state: AgentState) -> str:
        """只在两种情况下收工：策略喊停/预算用完。
        `stop_on_first_ok` 打开时才在拿到第一个合法解时提前收工
        （M1.5 的结论说继续调 SA 不会更好，但预算曲线需要看清这件事）。"""
        if state.get("done"):
            return "end"
        if state.get("used", 0) >= state.get("budget", 0):
            return "end"
        if stop_on_first_ok and state.get("best"):
            return "end"
        return "plan"

    g = StateGraph(AgentState)
    g.add_node("plan", plan)
    g.add_node("execute", execute)
    g.set_entry_point("plan")
    g.add_conditional_edges("plan",
                            lambda s: "end" if s.get("done") else "execute",
                            {"execute": "execute", "end": END})
    g.add_conditional_edges("execute", after_execute, {"plan": "plan", "end": END})
    return g.compile()


def run_agent(kernel: str, ii: int = 4, budget: int = 4, policy=None,
              store: Store = None, root=None, do_sim: bool = True,
              stop_on_first_ok: bool = False) -> dict:
    """不依赖 langgraph 也能跑的等价循环（langgraph 不可用时的兜底）。"""
    from agents import policy as policy_mod
    policy = policy or policy_mod.ScriptedPolicy()
    state: AgentState = {
        "kernel": kernel, "ii": ii, "budget": budget, "used": 0,
        "facts_text": "", "backend": "pathfinder", "params": {}, "pins": {},
        "forbidden": [], "history": [], "tried": [], "audit": {}, "best": {},
        "done": False, "stop_reason": "", "store": store, "root": root,
        "do_sim": do_sim, "log": [],
    }
    prob0 = load_problem(kernel, ii, root)
    state["facts_text"] = facts_text_of(prob0)
    state["n_nodes"] = prob0["n_nodes"]

    graph = build_graph(policy, store, stop_on_first_ok=stop_on_first_ok)
    if graph is not None:
        final = graph.invoke(state, {"recursion_limit": 400})
    else:                              # pragma: no cover
        final = _manual_loop(state, policy, store, root, do_sim, stop_on_first_ok)
    return {
        "kernel": kernel, "ii": ii, "budget": budget,
        "used": final.get("used", 0), "ok": bool(final.get("best")),
        "best": final.get("best") or {},
        "history": final.get("history") or [],
        "stop_reason": final.get("stop_reason", ""),
        "log": final.get("log") or [],
        "policy_failures": getattr(policy, "failures", []),
    }


def _manual_loop(state, policy, store, root, do_sim, stop_on_first_ok=False):
    """与 build_graph 里 plan/execute 同语义的线性实现，供没有 langgraph 时使用。"""
    from agents import audit as audit_mod
    from agents.engine import load_problem, run_attempt, summarize
    while state["used"] < state["budget"] and not state["done"]:
        act = policy.suggest(dict(state))
        if act.get("action") == "stop":
            state["stop_reason"] = act.get("why", "策略喊停")
            break
        backend, params, ii = state["backend"], dict(state["params"]), state["ii"]
        pins, forb = dict(state["pins"]), list(state["forbidden"])
        if act["action"] == "set_backend":
            backend = act["backend"]; params = dict(act.get("params") or {})
            ii = state["ii"] + act.get("ii_delta", 0)
        elif act["action"] == "constrain":
            pins = act.get("pins") or {}; forb = act.get("forbidden") or []
        prob = load_problem(state["kernel"], ii, root)
        att = run_attempt(state["kernel"], ii, backend, params=params, pins=pins,
                          forbidden=forb, root=root, do_sim=do_sim, prob=prob)
        summ = summarize(att)
        state["history"].append(summ)
        state["audit"] = audit_mod.audit(att, prob)
        state["used"] += 1
        if _better(att, state["best"]):
            state["best"] = dict(summ)
        if store is not None:
            store.record(kernel=state["kernel"], n_nodes=prob["n_nodes"],
                         n_edges=len(prob["edges"]), ii=att.ii, backend=backend,
                         params=params, ok=att.ok, cost=att.cost, lat=att.lat,
                         gate=state["audit"].get("gate", ""),
                         cls=state["audit"].get("cls", ""))
        if stop_on_first_ok and state["best"]:
            break
    return state
