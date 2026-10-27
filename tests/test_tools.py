"""Unit tests that need no toolchain: DFG parsing, arch parsing, placement rules.

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import textwrap

import pytest

from cgra_agent.placers.llm import PlacementSchemaError, extract_json_object
from cgra_agent.tools.facts import build_facts, facts_for_ii, parse_arch, parse_dfg
from cgra_agent.tools.validate import format_feedback, validate

DFG = """<?xml version="1.0"?>
<DFG>
  <Nodes>
    <Node idx="0" ASAP="0" ALAP="0" BB=""CONST="8">
      <OP>LOADB</OP>
      <Inputs></Inputs>
      <Outputs><Output idx="1" type="I1" nextiter="0"/></Outputs>
      <BasePointerName>A</BasePointerName>
    </Node>
    <Node idx="1" ASAP="1" ALAP="1" BB="bb1" CONST="4">
      <OP>ADD</OP>
      <Inputs><Input idx="0"/></Inputs>
      <Outputs><Output idx="2" type="I2" nextiter="0"/></Outputs>
    </Node>
    <Node idx="2" ASAP="2" ALAP="2" BB="" CONST="8">
      <OP>STOREB</OP>
      <Inputs><Input idx="1"/></Inputs>
      <Outputs></Outputs>
      <BasePointerName>A</BasePointerName>
    </Node>
  </Nodes>
</DFG>
"""

ARCH = {
    "ARCH": {
        "CGRA": {
            "SUBMODS": [
                {
                    "PATTERN": "GRID",
                    "DIMS": {"X": 2, "Y": 2},
                    "MODS": [
                        {"name": "PE_X0_Y0", "MOD": "PE_MEM", "X": 0, "Y": 0},
                        {"name": "PE_X0_Y1", "MOD": "PE_MEM", "X": 0, "Y": 1},
                        {"name": "PE_X1_Y0", "MOD": "PE", "X": 1, "Y": 0},
                        {"name": "PE_X1_Y1", "MOD": "PE", "X": 1, "Y": 1},
                    ],
                }
            ],
            "CONNECTIONS": {
                "PE_X0_Y0.MEMPORT": ["SPM_B0_WRAPPER_INS"],
                "PE_X0_Y1.MEMPORT": ["SPM_B1_WRAPPER_INS"],
            },
        },
        "SPM_B0_WRAPPER": {"DATA_LAYOUT": {"A": 0}},
        "SPM_B1_WRAPPER": {"DATA_LAYOUT": {"B": 128}},
        "FU": {"OPS": {"ADD": 1, "SELECT": 1, "CMERGE": 1, "MOVC": 1, "CMP": 1}},
        "FU_MEM": {"OPS": {"LOAD": 1, "LOADB": 1, "STORE": 1, "STOREB": 1, "ADD": 1}},
    }
}


@pytest.fixture()
def facts(tmp_path):
    dfg = tmp_path / "t.xml"
    dfg.write_text(DFG)
    arch = tmp_path / "t_mem.json"
    arch.write_text(json.dumps(ARCH))
    return build_facts(str(dfg), str(arch), 2)


def test_parse_dfg_survives_missing_space_and_links_both_ways(facts):
    nodes = {n["idx"]: n for n in facts["nodes"]}
    assert nodes[1]["const"] == 4  # BB=""CONST="4" was parsed as CONST
    assert nodes[0]["children"] == [[1, "I1", 0]]
    assert nodes[1]["parents"] == [[0, "I1", 0]]
    assert nodes[0]["bp"] == "A" and nodes[1]["bp"] == ""
    assert facts["n_edges"] == 2 and facts["n_inputs"] == 2


def test_parse_arch_banks_and_wiring(tmp_path):
    arch = tmp_path / "a.json"
    arch.write_text(json.dumps(ARCH))
    a = parse_arch(str(arch))
    assert a["xdim"] == 2 and a["ydim"] == 2
    assert a["pe_mem"] == [(0, 0), (0, 1)]
    assert a["pe_alu"] == [(1, 0), (1, 1)]
    assert a["var_bank"] == {"A": 0, "B": 1}
    assert a["wiring"] == "connections"
    assert a["pe_bank"] == {"0,0": 0, "0,1": 1}


def test_facts_carry_node_bank_and_capacity(facts):
    # LOADB node 0 and STOREB node 2 both read A, which lives in bank 0
    assert facts["node_bank"] == {0: 0, 2: 0}
    assert facts["phase_capacity"]["slots_total"] == 4 * 2
    assert facts_for_ii(facts, 3)["phase_capacity"]["slots_total"] == 4 * 3
    assert facts["min_ii"] >= 1


def _place(**kw):
    base = {"node": 0, "x": 0, "y": 0, "t": 0, "op": "LOADB"}
    base.update(kw)
    return base


GOOD = [
    _place(node=0, x=0, y=0, t=0, op="LOADB"),
    _place(node=1, x=1, y=0, t=1, op="ADD"),
    _place(node=2, x=0, y=0, t=2, op="STOREB"),
]


def test_validate_accepts_a_legal_placement(facts):
    r = validate(facts, [{"node": p["node"], "x": p["x"], "y": p["y"], "t": p["t"]} for p in GOOD], 4)
    assert r["ok"], r["errors"]
    assert r["stats"]["n_placed"] == 3


def test_validate_rejects_out_of_bounds_and_bad_phase(facts):
    bad = [dict(GOOD[0], x=5)] + GOOD[1:]
    r = validate(facts, bad, 2)
    assert not r["ok"] and any("outside" in e for e in r["errors"])

    bad = [dict(GOOD[0], t=2)] + GOOD[1:]
    r = validate(facts, bad, 2)
    assert not r["ok"] and any("outside 0..1" in e for e in r["errors"])


def test_validate_rejects_memory_op_on_plain_pe(facts):
    bad = [dict(GOOD[0], x=1)]
    r = validate(facts, bad, 2, require_complete=False)
    assert any("needs a memory PE" in e for e in r["errors"])


def test_validate_rejects_duplicate_slot_and_missing_pins(facts):
    bad = [dict(GOOD[0]), dict(GOOD[1], x=0, y=0, t=0)]
    r = validate(facts, bad, 2, require_complete=False)
    assert any("two nodes" in e for e in r["errors"])

    r = validate(facts, GOOD[:2], 2)
    assert any("without a pin" in e for e in r["errors"])


def test_validate_r5_checks_the_wired_bank(facts):
    # A lives in bank 0; the (0,1) memory PE is wired to bank 1, so this is illegal.
    bad = [dict(GOOD[0], y=1)]
    r = validate(facts, bad, 2, require_complete=False)
    assert any("must use bank 0" in e and "wired to bank 1" in e for e in r["errors"])


def test_validate_r5_skips_unknown_wiring(tmp_path):
    arch = json.loads(json.dumps(ARCH))
    arch["ARCH"]["CGRA"]["CONNECTIONS"] = {}
    p = tmp_path / "a.json"
    p.write_text(json.dumps(arch))
    dfg = tmp_path / "t.xml"
    dfg.write_text(DFG)
    f = build_facts(str(dfg), str(p), 2)
    r = validate(f, [dict(GOOD[0], y=1)], 2, require_complete=False)
    assert r["ok"]
    assert any("PE->bank wiring" in w for w in r["warnings"])


def test_validate_warns_on_select_same_phase(facts, tmp_path):
    dfg = textwrap.dedent(
        """<DFG><Nodes>
        <Node idx="0" ASAP="0" ALAP="0" BB=""><OP>MOVC</OP><Outputs><Output idx="2" type="I1" nextiter="0"/></Outputs></Node>
        <Node idx="1" ASAP="0" ALAP="0" BB=""><OP>MOVC</OP><Outputs><Output idx="2" type="I2" nextiter="0"/></Outputs></Node>
        <Node idx="2" ASAP="1" ALAP="1" BB=""><OP>SELECT</OP><Inputs><Input idx="0"/><Input idx="1"/></Inputs><Outputs></Outputs></Node>
        </Nodes></DFG>"""
    )
    p = tmp_path / "s.xml"
    p.write_text(dfg)
    arch = tmp_path / "a.json"
    arch.write_text(json.dumps(ARCH))
    f = build_facts(str(p), str(arch), 2)
    pins = [
        {"node": 0, "x": 1, "y": 0, "t": 0},
        {"node": 1, "x": 1, "y": 1, "t": 0},
        {"node": 2, "x": 1, "y": 0, "t": 1},
    ]
    r = validate(f, pins, 2)
    assert r["ok"]
    assert any("both enter at phase 0" in w for w in r["warnings"])


def test_format_feedback_mentions_errors(facts):
    r = validate(facts, [dict(GOOD[0], x=9)], 2)
    text = format_feedback(r)
    assert "rejected" in text and "outside" in text


@pytest.mark.parametrize(
    "text",
    [
        '{"pins": [], "ii": 2}',
        '```json\n{"pins": [], "ii": 2}\n```',
        'here you go: {"pins": [], "ii": 2} done',
    ],
)
def test_extract_json_object_tolerates_wrappers(text):
    assert extract_json_object(text)["ii"] == 2


def test_extract_json_object_rejects_prose():
    with pytest.raises(PlacementSchemaError):
        extract_json_object("I cannot place this graph.")
