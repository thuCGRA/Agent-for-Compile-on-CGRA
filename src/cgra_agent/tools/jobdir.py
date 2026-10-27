"""Isolated working directories, subprocess runs with timeouts, hashes, logs.

The mapper writes its artefacts next to its working directory and the
simulator wants a scratch directory, so every tool invocation gets its own
``JobDir``.  Nothing is allowed to write outside it; a RunBundle is then just
a copy of the files we want to keep.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class ToolError(RuntimeError):
    """A tool could not be run at all (missing binary, bad path)."""

    def __init__(self, message: str, *, log: Optional[str] = None):
        super().__init__(message)
        self.log = log


@dataclass
class RunResult:
    cmd: List[str]
    rc: int
    secs: float
    log_path: str
    timed_out: bool = False
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out

    def tail(self, n: int = 40) -> str:
        return tail(self.output, n)


def tail(text: str, n: int = 40) -> str:
    """Last ``n`` lines, for evidence fields that must stay small."""
    lines = (text or "").rstrip().splitlines()
    return "\n".join(lines[-n:])


class Deadline:
    """Monotonic wall-clock budget for one kernel run."""

    def __init__(self, seconds: float):
        self.seconds = float(seconds)
        self.started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return max(0.0, self.seconds - self.elapsed())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def clamp(self, timeout: float) -> float:
        """A tool timeout that never outlives the kernel budget."""
        return max(1.0, min(float(timeout), self.remaining()))


class JobDir:
    def __init__(self, path: os.PathLike | str, name: str = ""):
        self.path = Path(path).resolve()
        self.name = name or self.path.name
        self.path.mkdir(parents=True, exist_ok=True)
        self.logs: List[str] = []
        self._seq = 0

    # ------------------------------------------------------------------ paths
    def file(self, name: str | os.PathLike) -> str:
        return str(self.path / name)

    def dir(self, name: str) -> "JobDir":
        return JobDir(self.path / name, name=name)

    # -------------------------------------------------------------------- io
    def write_text(self, name: str, text: str) -> str:
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return str(p)

    def write_json(self, name: str, obj: Any) -> str:
        return self.write_text(name, json.dumps(obj, indent=2, sort_keys=False) + "\n")

    def read_json(self, name_or_path: str | os.PathLike, default: Any = None) -> Any:
        p = Path(name_or_path)
        if not p.is_absolute():
            p = self.path / p
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return default

    def copy_in(self, src: str | os.PathLike, name: Optional[str] = None) -> str:
        import shutil

        src = Path(src)
        dst = self.path / (name or src.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return str(dst)

    @staticmethod
    def sha256(path: str | os.PathLike) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    def sha256_file(self, name: str | os.PathLike) -> str:
        p = Path(name)
        if not p.is_absolute():
            p = self.path / p
        return self.sha256(p)

    # -------------------------------------------------------------- processes
    def run(
        self,
        cmd: Sequence[str],
        timeout: float = 600.0,
        log_name: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str | os.PathLike] = None,
        stdin_text: Optional[str] = None,
    ) -> RunResult:
        """Run ``cmd``, tee stdout+stderr into a log, kill it on timeout."""
        cmd = [str(c) for c in cmd]
        self._seq += 1
        log_name = log_name or ("run-%02d.log" % self._seq)
        log_path = self.file(log_name)
        full_env = dict(os.environ)
        if env:
            full_env.update({k: str(v) for k, v in env.items()})
        workdir = str(cwd or self.path)
        t0 = time.monotonic()
        timed_out = False
        output = ""
        try:
            proc = subprocess.run(
                cmd,
                cwd=workdir,
                env=full_env,
                input=stdin_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(1.0, float(timeout)),
            )
            output = proc.stdout or ""
            rc = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            raw = exc.stdout or ""
            output = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            rc = -9
        except FileNotFoundError as exc:
            output = "FileNotFoundError: %s" % exc
            rc = -2
        secs = round(time.monotonic() - t0, 3)
        header = "# cmd: %s\n# cwd: %s\n# rc: %s  secs: %s  timeout: %s\n" % (
            " ".join(cmd),
            workdir,
            rc,
            secs,
            timed_out,
        )
        Path(log_path).write_text(header + output)
        self.logs.append(log_path)
        return RunResult(
            cmd=list(cmd),
            rc=rc,
            secs=secs,
            log_path=log_path,
            timed_out=timed_out,
            output=output,
        )

    # ------------------------------------------------------------------ misc
    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "logs": [os.path.relpath(p, self.path) for p in self.logs],
        }
