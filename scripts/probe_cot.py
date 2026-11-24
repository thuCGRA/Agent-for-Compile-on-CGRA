#!/usr/bin/env python
"""把 LLMPolicy 真实看到的 prompt 拿去问一次模型，并把 reasoning_content 原文打出来。

目的：用户问"LLM 调度时到底想了什么、积累了经验没有"。
只看 content 是看不出所以然的，推理模型的思考全在 reasoning_content 里。

    python scripts/probe_cot.py            # 两个决策点：刚跑完 pathfinder / 刚跑完 sa
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import requests                                   # noqa: E402

from agents.engine import load_problem            # noqa: E402
from agents.graph import facts_text_of            # noqa: E402
from agents.policy import LLMPolicy               # noqa: E402


def _hist(backend, ii, ok, cost, lat, params, g1reason=""):
    return {"backend": backend, "ii": ii, "ok": ok, "cost": cost, "lat": lat,
            "n_placed": 20, "elapsed": 1.6, "params": params,
            "gates": {"G1": {"passed": ok, "reason": g1reason},
                      "G2": {"passed": ok, "reason": ""},
                      "G3": {"passed": True, "reason": ""},
                      "G4": {"passed": True, "reason": ""}},
            "pins": [], "error": ""}


def ask(pol, state, label):
    msgs = pol._prompt(state)
    payload = {"model": pol.client.model, "messages": msgs,
               "temperature": 0.0, "max_tokens": 3000}
    r = requests.post(pol.client.chat_url, json=payload,
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer %s" % pol.client.api_key},
                      timeout=300)
    d = r.json()
    msg = ((d.get("choices") or [{}])[0].get("message") or {})
    print("=" * 78)
    print("## %s" % label)
    print("-- user prompt --")
    print(msgs[1]["content"])
    print("-- reasoning_content (%d chars) --" % len(msg.get("reasoning_content") or ""))
    print((msg.get("reasoning_content") or "(无)")[:4000])
    print("-- content --")
    print(msg.get("content"))
    print("-- usage --", json.dumps(d.get("usage") or {}, ensure_ascii=False))
    return msg


def main():
    prob = load_problem("array_add", 4)
    facts = facts_text_of(prob)
    from experience.store import Store
    st = Store()
    pol = LLMPolicy(store=st)

    base = {"kernel": "array_add", "ii": 4, "budget": 3, "used": 1,
            "facts_text": facts, "n_nodes": prob["n_nodes"],
            "tried": [], "store": st}

    s1 = dict(base)
    s1["history"] = [_hist("pathfinder", 4, True, 36.0, 0, {})]
    s1["best"] = s1["history"][0]
    s1["audit"] = {"gate": "", "cls": "ok", "reason": "四门全过",
                   "suspects": [], "advice": ["记录这个配置"], "stop": False}
    ask(pol, s1, "决策点 1：刚跑完 pathfinder（cost 36, lat 0），还剩 2 次")

    s2 = dict(base)
    s2["used"] = 2
    s2["history"] = [_hist("pathfinder", 4, True, 36.0, 0, {}),
                     _hist("sa", 4, True, 43.0, 11,
                           {"optimize": True, "budget": 1000, "select": 1, "pick": 1})]
    s2["best"] = s2["history"][0]
    s2["audit"] = {"gate": "", "cls": "ok", "reason": "四门全过",
                   "suspects": [], "advice": ["记录这个配置"], "stop": False}
    ask(pol, s2, "决策点 2：跑完 pathfinder 36 与 sa 43（更差），还剩 1 次")


if __name__ == "__main__":
    main()
