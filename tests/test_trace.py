"""Trace discovery must prefer explicit config, then the repo, and never guess."""

from __future__ import annotations

import os

import pytest

from cgra_agent.tools.trace import TraceNotFound, find_traces, resolve_trace


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("trace\n")
    return path


def test_explicit_config_path_wins(tmp_path):
    repo = str(tmp_path / "repo")
    explicit = _touch(str(tmp_path / "memtraces" / "k_trace_explicit.txt"))
    _touch(os.path.join(repo, "toolchain/vendor/simulator/test/library/k_trace_0.txt"))
    found = find_traces(repo, "k", {"memtraces": os.path.dirname(explicit)})
    assert found and found[0].endswith("k_trace_explicit.txt")


def test_repo_layout_is_second_tier(tmp_path):
    repo = str(tmp_path / "repo")
    path = _touch(os.path.join(repo, "toolchain/vendor/simulator/test/library/k_trace_0.txt"))
    found = find_traces(repo, "k", {})
    assert found == [path]


def test_only_the_requested_kernel_matches(tmp_path):
    repo = str(tmp_path / "repo")
    _touch(os.path.join(repo, "toolchain/vendor/simulator/test/library/other_trace_0.txt"))
    assert find_traces(repo, "k", {}) == []


def test_trace_not_found_explains_the_blocker(tmp_path):
    repo = str(tmp_path / "repo")
    with pytest.raises(TraceNotFound) as excinfo:
        resolve_trace(repo, "k", {"memtraces": str(tmp_path / "missing.txt")})
    msg = str(excinfo.value)
    assert "gcc-multilib" in msg and "libc6-dev-i386" in msg
