"""The LLM placer: prompt -> json -> ``Placement``.

Schema failures raise ``PlacementSchemaError``; the pipeline turns that into a
re-ask (bounded), exactly like a validation error.  No retry logic lives here
so the call budget stays in one place (pipeline.py).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..interfaces import Pin, Placement
from ..llm.client import LLMClient
from ..llm.prompts import build_messages


class PlacementSchemaError(ValueError):
    """The model answered, but not with the placement contract."""


def extract_json_object(text: str) -> Dict[str, Any]:
    """Accept a bare object, a fenced block, or an object with prose around it."""
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    start = s.find("{")
    if start < 0:
        raise PlacementSchemaError("answer contains no json object: %r" % text[:200])
    try:
        obj, _end = json.JSONDecoder().raw_decode(s[start:])
    except ValueError as exc:
        raise PlacementSchemaError("invalid json (%s): %r" % (exc, text[:200]))
    if not isinstance(obj, dict):
        raise PlacementSchemaError("json answer is not an object")
    return obj


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PlacementSchemaError("%s is a boolean" % field)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise PlacementSchemaError("%s is not an integer: %r" % (field, value))


class LLMPlacer:
    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        seed: int = 0,
    ):
        self.client = client
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.seed = seed
        self.last_messages: List[dict] = []
        self.last_raw: str = ""
        self.last_usage: Dict[str, Any] = {}

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
        ii = int(ii)
        messages = build_messages(facts, ii, feedback=feedback, previous=previous)
        self.last_messages = messages
        text = self.client.chat(
            messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            seed=self.seed,
        )
        self.last_raw = text
        self.last_usage = dict(self.client.records[-1].get("usage") or {}) if self.client.records else {}
        obj = extract_json_object(text)
        raw_pins = obj.get("pins")
        if not isinstance(raw_pins, list):
            raise PlacementSchemaError("answer has no 'pins' array")
        pins: List[Pin] = []
        for i, p in enumerate(raw_pins):
            if not isinstance(p, dict):
                raise PlacementSchemaError("pins[%d] is not an object" % i)
            for key in ("node", "x", "y", "t"):
                if key not in p:
                    raise PlacementSchemaError("pins[%d] misses '%s'" % (i, key))
            pins.append(
                Pin(
                    node=_int(p["node"], "pins[%d].node" % i),
                    x=_int(p["x"], "pins[%d].x" % i),
                    y=_int(p["y"], "pins[%d].y" % i),
                    t=_int(p["t"], "pins[%d].t" % i),
                )
            )
        if not pins:
            raise PlacementSchemaError("answer has an empty 'pins' array")
        note = obj.get("reasoning")
        return Placement(
            pins=pins,
            ii=ii,
            note=str(note) if note is not None else "",
            source="llm",
        )
