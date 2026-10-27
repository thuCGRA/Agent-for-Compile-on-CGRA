"""Replay a fixed placement, e.g. a known good pin set or a baseline mapping.

It deliberately ignores feedback: a fixed placement that fails validation will
fail again, which is what makes it a control path rather than an agent.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..interfaces import Pin, Placement


class FixedPlacer:
    name = "fixed"

    def __init__(self, pins: list, note: str = "fixed placement"):
        self._pins = [
            Pin(node=int(p["node"]), x=int(p["x"]), y=int(p["y"]), t=int(p["t"]))
            for p in pins
        ]
        self.note = note
        self.last_messages = []
        self.last_raw = ""

    @classmethod
    def from_file(cls, path: str) -> "FixedPlacer":
        data = json.load(open(path))
        pins = data.get("pins") if isinstance(data, dict) else data
        return cls(pins or [], note="replay of %s" % path)

    def place(
        self,
        facts: Dict[str, Any],
        arch: Dict[str, Any],
        ii: int,
        budget: Optional[Dict[str, Any]] = None,
        *,
        feedback: Optional[str] = None,
        previous: Optional[Any] = None,
    ) -> Placement:
        # Re-express t modulo the II the pipeline is currently trying.
        pins = [Pin(node=p.node, x=p.x, y=p.y, t=p.t % max(1, int(ii))) for p in self._pins]
        return Placement(pins=pins, ii=int(ii), note=self.note, source="fixed")
