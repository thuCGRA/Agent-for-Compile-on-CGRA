"""Rule placer: cheap, non-searching, must produce a complete phase-local placement."""

from cgra_agent.placers.rule import RulePlacer
from cgra_agent.tools.facts import MEMORY_OPS


def _facts():
    nodes = [
        {"idx": 0, "op": "LOAD", "asap": 0, "lat": 2, "parents": [], "children": [[1, 0, 0]]},
        {"idx": 1, "op": "ADD", "asap": 2, "lat": 1, "parents": [[0, 0, 0]], "children": [[2, 0, 0]]},
        {"idx": 2, "op": "STORE", "asap": 3, "lat": 2, "parents": [[1, 0, 0]], "children": []},
    ]
    return {
        "nodes": nodes,
        "pe_alu": [(1, 0), (2, 0), (1, 1), (2, 1)],
        "pe_mem": [(0, 0), (0, 1)],
        "bank_vars": {"SPM_B0_WRAPPER": ["A"], "SPM_B1_WRAPPER": ["B"]},
        "bank_pes": {},
        "node_bank": {"0": "SPM_B0_WRAPPER", "2": "SPM_B1_WRAPPER"},
    }


def test_rule_places_every_node_once():
    p = RulePlacer().place(_facts(), {}, 4)
    assert sorted(pin.node for pin in p.pins) == [0, 1, 2]
    assert len({(pin.x, pin.y, pin.t) for pin in p.pins}) == 3
    assert p.source == "rules"


def test_rule_phase_is_asap_mod_ii():
    p = RulePlacer().place(_facts(), {}, 4)
    phases = {pin.node: pin.t for pin in p.pins}
    assert phases[0] == 0
    assert phases[1] == 2
    assert phases[2] == 3


def test_rule_memory_nodes_on_bank_pes():
    p = RulePlacer().place(_facts(), {}, 4)
    mem_pes = {(pin.x, pin.y) for pin in p.pins if pin.node in (0, 2)}
    assert mem_pes <= {(0, 0), (0, 1)}


def test_rule_raises_on_full_pe_bank():
    facts = _facts()
    facts["pe_mem"] = [(0, 0)]
    facts["node_bank"] = {"0": "SPM_B0_WRAPPER", "2": "SPM_B0_WRAPPER"}
    for node in facts["nodes"]:
        node["asap"] = 0
    from cgra_agent.placers.rule import RuleError

    try:
        RulePlacer().place(facts, {}, 4)
    except RuleError as exc:
        assert "memory PE" in str(exc)
    else:
        raise AssertionError("expected RuleError")
