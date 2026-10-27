"""Artifact honesty: a mapper that says ok without a bitstream is a toolchain bug."""

from cgra_agent.interfaces import MapReport
from cgra_agent.tools.jobdir import RunResult
from cgra_agent.tools.mapper import check_artifacts


def _run(rc: int = 0, timed_out: bool = False) -> RunResult:
    return RunResult(cmd=["mapper"], rc=rc, secs=0.1, log_path="",
                     timed_out=timed_out, output="")


def test_ok_without_bitstream_is_toolchain():
    rep = check_artifacts(MapReport(ok=True, stage="done"), "", _run())
    assert not rep.ok
    assert rep.failure_class == "toolchain"
    assert "no bitstream" in rep.error


def test_ok_with_bitstream_is_kept(tmp_path):
    binary = tmp_path / "a_binary.bin"
    binary.write_text("bitstream")
    rep = check_artifacts(MapReport(ok=True, stage="done"), str(binary), _run())
    assert rep.ok
    assert rep.failure_class == ""


def test_signal_without_ok_is_toolchain():
    rep = check_artifacts(MapReport(ok=False, stage="route"), "", _run(rc=-6))
    assert rep.failure_class == "toolchain"
    assert "signal" in rep.error


def test_failure_stage_classification():
    assert check_artifacts(
        MapReport(ok=False, stage="placement"), "", _run(rc=1)).failure_class == "semantic"
    assert check_artifacts(
        MapReport(ok=False, stage="route"), "", _run(rc=1)).failure_class == "runtime"
