import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cgra_agent.tools.facts import build_facts
from cgra_agent.tools.feasible import describe, feasible_pes, nodes_at, nodes_in_text
from cgra_agent.interfaces import Pin, Placement


def _facts():
    return {
        "pe_alu": [(1, 0), (2, 0)],
        "pe_mem": [(0, 0), (0, 1)],
        "nodes": [
            {"idx": 3, "op": "ADD"},
            {"idx": 4, "op": "LOAD"},
        ],
    }


def test_feasible_pes_split_on_column():
    pes = feasible_pes(_facts())
    assert pes[3] == [(1, 0), (2, 0)]
    assert pes[4] == [(0, 0), (0, 1)]


def test_describe_names_nodes_and_forbids_others():
    placement = Placement(pins=[Pin(node=3, x=2, y=0, t=1), Pin(node=4, x=0, y=0, t=0)])
    text = describe(_facts(), 4, [3], placement, header="rejected")
    assert "node 3 (ADD)" in text
    assert "current (2,0,t=1)" in text
    assert "Change ONLY these nodes" in text
    assert ">=" in text
    assert "equal" not in text.lower()


def test_nodes_at_matches_coordinates():
    placement = Placement(pins=[Pin(node=3, x=2, y=0, t=1)])
    assert nodes_at("PE (2,0) at cycle 5", placement) == [3]


def test_nodes_in_text():
    assert nodes_in_text("node 7 and Node 7 and node#9") == [7, 9]