"""策略层：只发意图（JSON），不碰进程、不碰文件。

两个实现：
- ScriptedPolicy：确定性阶梯，作为对照组，也是 LLM 失败时的兜底。
  它不"笨"——它编码了我们目前知道的最优顺序（先 pathfinder，再 SA，再抬 II）。
- LLMPolicy：把 facts 摘要 + 历史 + 归因喂给模型，要它返回一个动作 JSON。
  模型输出必须过 schema 校验；校验不过算装置失败，回落到 ScriptedPolicy，
  并且明确记账（不把"解析失败"记成"模型决策差"）。
"""
from __future__ import annotations

import json
from typing import Optional

from llm.client import LLMClient, LLMError, extract_json

ACTIONS = ("set_backend", "constrain", "set_ii", "stop")

# 确定性阶梯。key 用于去重：同一 (backend, ii, params) 不重复跑。
LADDER = [
    {"action": "set_backend", "backend": "pathfinder", "params": {}, "ii_delta": 0,
     "why": "先拿 PathFinder 基线：它最稳，且我们的指标就是它的解"},
    {"action": "set_backend", "backend": "sa", "ii_delta": 0,
     "params": {"optimize": True, "budget": 1000},
     "why": "SA 跑满 1000 次移动，看能不能比热启动好"},
    {"action": "set_backend", "backend": "pathfinder", "ii_delta": 1,
     "params": {}, "why": "抬一级 II 松时序，常能救回 SELECT 同拍"},
    {"action": "set_backend", "backend": "sa", "ii_delta": 0,
     "params": {"optimize": True, "budget": 1000, "select": 1, "pick": 1},
     "why": "SA + blame 挑节点 + 距离挑候选，换邻域再试一次"},
    {"action": "stop", "why": "阶梯跑完，没有更好的了"},
]


def _key(action: dict, ii: int) -> str:
    return json.dumps({"b": action.get("backend"), "ii": ii + action.get("ii_delta", 0),
                       "p": action.get("params") or {},
                       "pins": sorted((action.get("pins") or {}).items())},
                      sort_keys=True, ensure_ascii=False)


class ScriptedPolicy:
    name = "scripted"

    def __init__(self):
        self._i = 0

    def suggest(self, state: dict) -> dict:
        base_ii = state["ii"]
        tried = set(state.get("tried", []))
        while self._i < len(LADDER):
            act = dict(LADDER[self._i])
            self._i += 1
            k = _key(act, base_ii)
            if act["action"] == "stop":
                return act
            if k in tried:
                continue
            return act
        return {"action": "stop", "why": "阶梯用完"}


class LLMPolicy:
    name = "llm"

    def __init__(self, client: Optional[LLMClient] = None, no_thinking: bool = False,
                 store=None):
        self.client = client or LLMClient(no_thinking=no_thinking)
        # 备用客户端：推理通道吃光 max_tokens 时正文会是空的（finish_reason=length）。
        # 这不是"模型没主意"，是装置故障；降级为关思考再问一次，并把这次降级记账。
        self.quiet_client = None if no_thinking else LLMClient(no_thinking=True)
        self.fallback = ScriptedPolicy()
        self.store = store             # 经验库：只读注入 prompt，不参与判据
        self.failures = []            # 装置失败（解析失败/端点错误），单独记账

    # ------------------------------------------------------------------ prompt
    def _prompt(self, state: dict) -> list:
        facts = state.get("facts_text") or ""
        hist = state.get("history") or []
        aud = state.get("audit") or {}
        lines = []
        for i, h in enumerate(hist[-6:], 1):
            g = h.get("gates") or {}
            if h.get("ok"):
                # 通过的那一次没有"首个失败门"——这里以前错写成 G1，
                # 模型会当成"G1 挂了但又 ok=True"，白白花几百字纠结（实测过）。
                first_fail = "四门全过"
            else:
                first_fail = next((k for k in ("G1", "G2", "G3", "G4")
                                   if not g.get(k, {}).get("passed", True)), "-")
            lines.append(
                "%d) backend=%s ii=%d params=%s -> ok=%s lat=%s cost=%s | %s%s"
                % (i, h["backend"], h["ii"], h["params"], h["ok"],
                   h["lat"], h["cost"], first_fail,
                   ("：" + (g.get("G1", {}).get("reason") or "")[:70])
                   if not h.get("ok") else ""))
        best = state.get("best") or {}
        exp = self._recall_lines(state)
        system = (
            "你在调度一个 CGRA 映射器（Morpher）。你不决定任何节点的坐标，"
            "只能选后端、给约束、调 II。\n"
            "唯一的功能真值是仿真器（G1）。mapper 自己说 Map Success 不算过。\n"
            "已知事实：\n"
            "- SA 的热启动是贪心的 PathFinder 式放置；热启动可行时上游 SA 几乎不动。\n"
            "- 我们的 cost = detour + hops + select_pairs，SA 自己的目标函数不含延迟/布线，"
            "所以 SA 常常 cost 更差、lat 更差。\n"
            "- II 低于 RecMinII 会被 mapper 自动抬回（本项目 3 个 kernel 的 RecMinII 都是 4），"
            "把 ii 设成比它小是空操作，不要这么做。\n"
            "- 已经有合法解时，目标是降低 cost（其次 lat）；重复跑同配置是浪费预算。\n"
            "只能返回一个 JSON 对象，不要解释。字段：\n"
            '  {"action": "set_backend", "backend": "pathfinder|sa", '
            '"params": {...}, "ii": <int>, "why": "<一句>"}\n'
            '  {"action": "constrain", "pins": {"<idx>": [x,y]}, '
            '"forbidden": [[idx,x,y]], "why": "..."}\n'
            '  {"action": "stop", "why": "..."}\n'
            "SA 可用 params：optimize(bool), budget(int), select(0/1), pick(0/1), "
            "cost_mode(0/1)。\n"
            "预算有限：每多一次尝试就少一次机会。已经测过的配置不要重复。"
        )
        best_txt = ("cost=%s lat=%s（backend=%s）" % (best.get("cost"), best.get("lat"),
                                                     best.get("backend"))
                    if best else "（还没有合法解）")
        user = (
            "## 当前问题\n%s\n\n## 已尝试（按时间顺序）\n%s\n\n"
            "## 当前最好解\n%s\n\n%s"
            "## 上一次尝试的归因\n%s\n\n## 还能用的预算\n剩余尝试次数：%d\n\n"
            "给出下一步动作的 JSON。"
            % (facts, "\n".join(lines) or "（无）", best_txt, exp,
               json.dumps(aud, ensure_ascii=False) if aud else "（无）",
               max(0, state.get("budget", 0) - state.get("used", 0)))
        )
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    def _recall_lines(self, state: dict) -> str:
        """把经验库里同规模历史案例塞进 prompt（只读，不影响判据）。"""
        if self.store is None:
            return ""
        try:
            rows = self.store.recall(state.get("kernel", ""),
                                     int(state.get("n_nodes") or 0),
                                     int(state.get("ii") or 4), limit=4)
        except Exception:                      # 经验库坏了不该拦住决策
            return ""
        if not rows:
            return ""
        lines = ["## 历史相似案例（经验库，跨运行）"]
        for r in rows:
            lines.append("  - %s %s ii=%s -> ok=%s cost=%s lat=%s%s"
                         % (r["backend"], r.get("params"), r.get("ii"), r.get("ok"),
                            r.get("cost"), r.get("lat"),
                            ("（%s）" % r["cls"]) if r.get("cls") not in ("", "ok") else ""))
        return "\n".join(lines) + "\n\n"

    # ------------------------------------------------------------------ decide
    def suggest(self, state: dict) -> dict:
        msgs = self._prompt(state)
        try:
            text = self._ask(msgs)
            obj = extract_json(text)
            act = self._validate(obj, state)
            if act is None:
                self.failures.append({"kind": "bad_json", "raw": text[:300]})
                return self._fallback(state, "模型输出不是合法动作 JSON")
            # 预算有限：已经跑过的 (backend, ii, params, pins) 不再重跑
            if act.get("action") in ("set_backend", "constrain"):
                k = _key(act, state["ii"])
                if k in set(state.get("tried") or []):
                    self.failures.append({"kind": "duplicate_action",
                                          "action": act.get("action")})
                    return self._fallback(state, "重复动作")
            return act
        except LLMError as exc:
            self.failures.append({"kind": "llm_error", "error": str(exc)[:300]})
            return self._fallback(state, "端点失败")

    def _ask(self, msgs) -> str:
        """先按正常预算问；空正文（推理烧光 token）就关思考再问一次。"""
        try:
            return self.client.chat(msgs, max_tokens=3000, tag="agent")
        except LLMError as exc:
            if self.quiet_client is None or "空正文" not in str(exc):
                raise
            self.failures.append({"kind": "downgrade_no_thinking",
                                  "error": str(exc)[:200]})
            return self.quiet_client.chat(msgs, max_tokens=1200, tag="agent-quiet")

    def _fallback(self, state: dict, why: str) -> dict:
        act = self.fallback.suggest(state)
        act = dict(act)
        act["why"] = "[兜底:%s] %s" % (why, act.get("why", ""))
        act["fallback"] = True
        return act

    def _validate(self, obj, state) -> Optional[dict]:
        if not isinstance(obj, dict):
            return None
        a = obj.get("action")
        if a not in ACTIONS:
            return None
        if a == "stop":
            return {"action": "stop", "why": str(obj.get("why", ""))[:200]}
        if a == "set_backend":
            b = obj.get("backend")
            if b not in ("pathfinder", "sa"):
                return None
            params = obj.get("params") or {}
            if not isinstance(params, dict):
                return None
            # 白名单，防止把任意键塞进命令行
            allow = {"optimize", "budget", "select", "pick", "cost_mode", "force_anneal"}
            params = {k: v for k, v in params.items() if k in allow}
            # 归一化到"实际会跑的命令行"，否则 pathfinder 带着 SA 的 params 会被
            # 记成一次不同的尝试，去重失效、白烧预算（踩过）。
            from .engine import _norm_params
            params = _norm_params(b, params)
            ii = obj.get("ii")
            base = state["ii"]
            if ii is not None:
                try:
                    ii = int(ii)
                except (TypeError, ValueError):
                    return None
                ii = max(1, min(ii, 16))
                delta = ii - base
            else:
                delta = 0
            return {"action": "set_backend", "backend": b, "params": params,
                    "ii_delta": delta, "ii_abs": ii,
                    "why": str(obj.get("why", ""))[:200]}
        if a == "constrain":
            pins = {}
            for k, v in (obj.get("pins") or {}).items():
                try:
                    pins[int(k)] = [int(v[0]), int(v[1])]
                except (TypeError, ValueError, IndexError):
                    return None
            forb = []
            for item in (obj.get("forbidden") or []):
                try:
                    forb.append([int(item[0]), int(item[1]), int(item[2])])
                except (TypeError, ValueError, IndexError):
                    return None
            return {"action": "constrain", "pins": pins, "forbidden": forb,
                    "why": str(obj.get("why", ""))[:200]}
        return None
