"""经验库：SQLite 里记每一次尝试，agent 下次遇到相似问题时能查。

写进去的是**可查的特征 + 结论**，不是整份 placement：
    (kernel, n_nodes, n_edges, ii, backend, params, ok, cost, lat, gate, cls)
查询按 (n_nodes 相近, ii 相同) 找，返回历史上哪些配置过了、代价多少。

为什么不做 embedding：现在样本量是几十条、kernel 只有 3 个，
精确匹配 + 节点数区间就够了；等 kernel 上到几十个再谈向量检索。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import List, Optional

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "work", "experience.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kernel TEXT NOT NULL,
    n_nodes INTEGER,
    n_edges INTEGER,
    ii INTEGER,
    backend TEXT,
    params TEXT,
    ok INTEGER,
    cost REAL,
    lat INTEGER,
    gate TEXT,
    cls TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_kn ON cases(kernel, n_nodes, ii);
"""


class Store:
    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def record(self, *, kernel: str, n_nodes: int, n_edges: int, ii: int,
               backend: str, params: dict, ok: bool, cost: float, lat: int,
               gate: str = "", cls: str = "", note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO cases (ts,kernel,n_nodes,n_edges,ii,backend,params,ok,cost,lat,gate,cls,note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), kernel, n_nodes, n_edges, ii,
             backend, json.dumps(params, sort_keys=True, ensure_ascii=False),
             1 if ok else 0, float(cost or 0.0), int(lat or 0), gate, cls, note))
        self.conn.commit()

    def recall(self, kernel: str, n_nodes: int, ii: int, tol: int = 4,
               limit: int = 12) -> List[dict]:
        cur = self.conn.execute(
            "SELECT backend,params,ok,cost,lat,gate,cls,ii,n_nodes FROM cases"
            " WHERE ii=? AND n_nodes BETWEEN ? AND ?"
            " ORDER BY ok DESC, cost ASC LIMIT ?",
            (ii, n_nodes - tol, n_nodes + tol, limit))
        out = []
        for b, p, ok, cost, lat, gate, cls, rii, rn in cur.fetchall():
            out.append({"backend": b, "params": json.loads(p or "{}"),
                        "ok": bool(ok), "cost": cost, "lat": lat,
                        "gate": gate, "cls": cls, "ii": rii, "n_nodes": rn})
        return out

    def stats(self) -> dict:
        cur = self.conn.execute("SELECT COUNT(*), SUM(ok) FROM cases")
        n, ok = cur.fetchone()
        return {"cases": n or 0, "ok": ok or 0}
