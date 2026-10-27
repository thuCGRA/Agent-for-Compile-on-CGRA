"""LLM experiment plumbing: no-endpoint guard, cache keys, fixture replay."""

import json
import os
import subprocess
import sys

import pytest

from cgra_agent.llm.cache import LLMCache, cache_key, prompt_sha
from cgra_agent.placers.llm import extract_json_object

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "llm_placement_response.json")


def test_no_endpoint_prints_and_exits_nonzero():
    env = {k: v for k, v in os.environ.items() if k != "CGRA_LLM_BASE_URL"}
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "eval_llm_placement.py")],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert proc.returncode != 0
    assert "NO_ENDPOINT" in (proc.stdout + proc.stderr)


def test_cache_key_depends_on_prompt_and_dfg():
    base = cache_key("array_add", "a" * 64, 4, prompt_sha([{"role": "user", "content": "x"}]))
    other_prompt = cache_key("array_add", "a" * 64, 4, prompt_sha([{"role": "user", "content": "y"}]))
    other_dfg = cache_key("array_add", "b" * 64, 4, prompt_sha([{"role": "user", "content": "x"}]))
    assert base != other_prompt
    assert base != other_dfg


def test_cache_roundtrip(tmp_path):
    cache = LLMCache(str(tmp_path))
    key = "k" * 64
    assert cache.get(key) is None
    cache.put(key, '{"pins": []}', usage={"prompt_tokens": 3})
    hit = cache.get(key)
    assert hit["response"] == '{"pins": []}'
    assert hit["usage"]["prompt_tokens"] == 3


def test_mock_fixture_replays_to_a_placement_payload():
    data = json.load(open(FIXTURE, encoding="utf-8"))
    payload = extract_json_object(data["response"])
    if isinstance(payload, str):
        payload = json.loads(payload)
    pins = payload.get("pins") or []
    assert len(pins) == 20
    assert {p["node"] for p in pins} >= {0, 1, 2, 19}
    assert all(0 <= p["t"] < 4 for p in pins)
