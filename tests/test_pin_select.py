"""pin_select 规则的单测（不依赖工具链）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cgra_agent.placers.pin_select import RULES, select


def _facts():
    # 0 -> 1 -> 3，0 -> 2 -> 3，4 独立；lat：1 拍
    nodes = [
        {"idx": 0, "op": "ADD", "lat": 1, "asap": 0, "parents": [], "children": [[1, 0, 0], [2, 0, 0]]},
        {"idx": 1, "op": "ADD", "lat": 1, "asap": 1, "parents": [[0, 0, 0]], "children": [[3, 0, 0]]},
        {"idx": 2, "op": "ADD", "lat": 1, "asap": 1, "parents": [[0, 0, 0]], "children": [[3, 0, 0]]},
        {"idx": 3, "op": "ADD", "lat": 1, "asap": 2, "parents": [[1, 0, 0], [2, 0, 0]], "children": []},
        {"idx": 4, "op": "LOAD", "lat": 2, "asap": 0, "parents": [], "children": []},
    ]
    return {"n_nodes": 5, "ii": 4, "nodes": nodes, "bank_vars": {0: ["A", "C"]}}


def test_rules_are_fixed_set():
    assert set(RULES) == {"degree", "critical", "bank"}


def test_select_counts():
    facts = _facts()
    assert select(facts, 1.0, "degree") == [0, 1, 2, 3, 4]
    assert select(facts, 0.0, "degree") == []
    assert len(select(facts, 0.4, "degree")) == 2  # round(0.4 * 5)
    assert len(select(facts, 0.25, "bank")) == 1


def test_degree_puts_busiest_first():
    facts = _facts()
    picked = select(facts, 0.4, "degree")
    # 0 的度最大（出 2），应排第一
    assert 0 in picked


def test_critical_excludes_loose_node():
    facts = _facts()
    picked = select(facts, 0.8, "critical")
    assert len(picked) == 4
    assert 0 in picked and 3 in picked
    assert 4 not in picked


def test_bank_prefers_memory_and_neighbours():
    facts = _facts()
    picked = select(facts, 0.4, "bank")
    assert 4 in picked
    assert picked[0] == 4
