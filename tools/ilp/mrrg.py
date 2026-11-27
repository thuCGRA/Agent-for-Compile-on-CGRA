"""Port-level MRRG built directly from a Morpher ADL description.

The hand-written arc model in ``ilp_router.py`` kept diverging from what the
mapper actually does, because it encoded HyCUBE's port table by hand.  This
module instead *derives* the graph from ``arch.json``:

* ``PE.CONNECTIONS``  -- intra-PE wiring (crossbars, input/output registers)
* ``CGRA.SUBMODS[0].CONNECTIONS`` -- the inter-PE grid, as coordinate templates
* the datapath tail   -- ``FU0.DP0_Ix -> FU0.DP0.Ix -> FU0.DP0.T -> FU0.DP0_T``

A node is ``(x, y, t, port)`` with ``t`` modulo II.  An arc costs the number of
cycles it burns: 0 for combinational wiring, 1 for stepping through a register.

Register naming follows the mapper's own route dump (``*.routeInfo.log``):
a register appears as two ports, ``<R>_RO`` (read side) and ``<R>_RI`` (write
side).  ``<R>_RO -> <R>_RI`` advances one cycle, ``<R>_RI -> <R>_RO`` does not.
Reproducing those exact names is what lets the generated graph be checked
against ground truth by ``validate_mrrg.py``.
"""

import json
import re

REG_PORTS = ("NR", "ER", "WR", "SR", "TREG")
DIRS = ("NORTH", "EAST", "WEST", "SOUTH")
OPERANDS = ("I1", "I2", "P")

_ARITH = re.compile(r"^[XY0-9+\- ]+$")


class MrrgError(Exception):
    pass


def _eval(expr, x, y):
    """Evaluate a coordinate template such as ``"Y-1"`` for one grid point."""
    if not isinstance(expr, str):
        return int(expr)
    if not _ARITH.match(expr):
        raise MrrgError("refusing to evaluate coordinate expression %r" % (expr,))
    return int(eval(expr, {"__builtins__": {}}, {"X": x, "Y": y}))


def _load(arch):
    if isinstance(arch, str):
        with open(arch) as fh:
            arch = json.load(fh)
    return arch.get("ARCH", arch)


def _grid(arch):
    for sub in arch["CGRA"].get("SUBMODS", []):
        if sub.get("PATTERN") == "GRID":
            return sub
    raise MrrgError("no GRID submodule in CGRA.SUBMODS")


def _port(name):
    """``"THIS.NORTH_I"`` -> ``"NORTH_I"``; ``"FU0.DP0_T"`` is already absolute."""
    return name[5:] if name.startswith("THIS.") else name


def _fu_name(arch):
    for fu in arch["PE"].get("SUBMODS", {}).get("FU", []):
        return fu.get("name", "FU0")
    return "FU0"


def build(arch, ii, xdim=None, ydim=None):
    """Return ``(nodes, arcs, meta)``.

    ``nodes`` is the set of ``(x, y, t, port)`` tuples, ``arcs`` maps
    ``(u, v)`` to the cycle cost.  ``meta`` carries the per-cell module kind and
    the port names a route starts from / must reach.
    """
    arch = _load(arch)
    grid = _grid(arch)
    dims = grid["DIMS"]
    nx = xdim or int(dims["X"])
    ny = ydim or int(dims["Y"])
    fu = _fu_name(arch)

    kind = {}
    for mod in grid.get("MODS", []):
        kind[(int(mod["X"]), int(mod["Y"]))] = mod.get("MOD", "PE")
    for x in range(nx):
        for y in range(ny):
            kind.setdefault((x, y), "PE")

    # 1. PE-level wiring, straight out of the ADL.  A port that names a
    #    register becomes the read side (<R>_RO); the write side is added below.
    intra = []
    for src, dsts in arch["PE"].get("CONNECTIONS", {}).items():
        sport = _port(src)
        if sport in REG_PORTS:
            sport += "_RO"
        for dst in dsts:
            dport = _port(dst)
            if dport in REG_PORTS:
                dport += "_RO"
            intra.append((sport, dport, 0))

    # 2. Registers are a two-port loop: RO --(+1 cycle)--> RI --(0)--> RO.
    #    Both spellings appear in the mapper's route dump, and either side may
    #    feed the crossbar (a latched value is readable at RI just as at RO),
    #    so mirror every outgoing edge onto the write side too.
    mirrored = []
    for sport, dport, cost in intra:
        if sport.endswith("_RO"):
            mirrored.append((sport[:-3] + "_RI", dport, cost))
    intra.extend(mirrored)
    for reg in REG_PORTS:
        intra.append((reg + "_RO", reg + "_RI", 1))
        intra.append((reg + "_RI", reg + "_RO", 0))

    # 3. Datapath tail: the operand enters the FU, the op runs, the result
    #    leaves on DP0_T.  These are the names the mapper prints.
    # NO_DATAPATH_THROUGH: only the operand hand-off.  Letting a path run
    # FU0.DP0.Ix -> FU0.DP0.T -> FU0.DP0_T would let it exit on the ALU output of
    # a PE where some other node is executing, and the mapper rejects exactly
    # that ("edge 5 -> 20019 collides ... FU0.DP0.T@4 by 2").  DP0_T is still the
    # source of every path: that one belongs to the producer itself.
    # NO_ALU_BORROW: a value may not walk into a datapath and out of its ALU
    # output -- that port belongs to the node executing on the PE, and the mapper
    # rejects the squat ("edge 5 -> 20019 collides ... FU0.DP0.T@4 by 2").  The
    # DP0.T -> DP0_T hand-off stays because it appears in the mapper's own dumps.
    for op in OPERANDS:
        intra.append((fu + ".DP0_" + op, fu + ".DP0." + op, 0))
    intra.append((fu + ".DP0.T", fu + ".DP0_T", 0))

    # 3b. ALU_I: the same-PE combinational feedback path.  A PE can feed its own
    #     result straight back into its operand port in the same cycle; the
    #     simulator encodes this as xbar input ALU_I ("100") and the mapper
    #     records it as a single-port chain with no intermediate hop.  The ADL
    #     has no such connection (FU0.DP0_T only reaches the four output ports
    #     and TREG), so without this arc a port-graph router is forced through
    #     TREG -- one extra cycle, i.e. xbar input RES_I ("101") -- and reports
    #     far more register steps than the mapper actually needs.
    # ALU_I_DISABLED: the same-PE feedback arc is deliberately NOT modelled.
    # The simulator's ALU_I input takes ALU_O unconditionally (CGRATile.cpp:396
    # ``I1_tobewritten=ALU_O``, then updateRegisters pushes it whenever
    # ALU_O.first), so a value delivered through ALU_I is *always* valid.  A
    # SELECT must see exactly one valid operand (assert !(I1isvalid &&
    # I2isvalid), CGRATile.cpp:168), and with ALU_I the local operand is
    # unconditionally valid, so the assert fires as soon as the network operand
    # also lands.  The mapper itself routes a same-PE operand through TREG
    # (encoding RES_I = "101", conditional on tregwen) and never relies on
    # ALU_I; we do the same and let CYCLE_BUDGET force d >= 1 for same-PE edges.
    # for op in OPERANDS:
    #     intra.append((fu + ".DP0_T", fu + ".DP0_" + op, 0))

    nodes = set()
    arcs = {}
    for (x, y) in kind:
        for t in range(ii):
            for sport, dport, cost in intra:
                u = (x, y, t, sport)
                v = (x, y, (t + cost) % ii, dport)
                nodes.add(u)
                nodes.add(v)
                arcs[(u, v)] = cost

    # 4. Inter-PE grid, expanded from the coordinate templates.
    for conn in grid.get("CONNECTIONS", []):
        for x in range(nx):
            for y in range(ny):
                tx = _eval(conn["TO_X"], x, y)
                ty = _eval(conn["TO_Y"], x, y)
                if not (0 <= tx < nx and 0 <= ty < ny):
                    continue
                for t in range(ii):
                    u = (x, y, t, _port(conn["FROM_PORT"]))
                    v = (tx, ty, t, _port(conn["TO_PORT"]))
                    arcs[(u, v)] = 0

    meta = {
        "xdim": nx,
        "ydim": ny,
        "ii": ii,
        "kind": kind,
        "fu": fu,
        "source": fu + ".DP0_T",
        # SINKS_AT_DP: end on the datapath operand port.  printHyCUBEBinary
        # inspects dp->getInPort(op) (``FU0.DP0.I1``); a chain that stops at the
        # FU-level ``FU0.DP0_I1`` leaves that port unassigned and the operand gets
        # encoded as "111" (invalid), which the simulator then executes.
        "sinks": dict((op, fu + ".DP0." + op) for op in OPERANDS),
        "intra": intra,
    }
    return nodes, arcs, meta


def describe(nodes, arcs):
    regs = sum(1 for _k, c in arcs.items() if c)
    return "%d node(s), %d arc(s) (%d register steps)" % (len(nodes), len(arcs), regs)


def resources(arcs):
    """Map each resource (port, x, y, t) to the arcs that occupy it.

    Only non-register ports are capacity-1 per cycle; a register is also
    capacity-1 but it is already serialised by the RO/RI loop, so both are
    listed here and the caller decides.
    """
    res = {}
    for (u, v) in arcs:
        for node in (u, v):
            res.setdefault((node[3], node[0], node[1], node[2]), []).append((u, v))
    return res
