"""后端注册表。agent 通过名字选后端，不直接 import 具体类。"""
from .base import Backend, BackendResult, Hints
from .pathfinder import PathFinderBackend
from .sa import SABackend

_CLASSES = {
    "pathfinder": PathFinderBackend,
    "sa": SABackend,
}


def make(name, **kw):
    if name not in _CLASSES:
        raise KeyError("未知后端 %r，可选 %s" % (name, sorted(_CLASSES)))
    return _CLASSES[name](**kw)


def available():
    out = {}
    for name, cls in _CLASSES.items():
        ok, why = cls().available()
        out[name] = {"ok": ok, "reason": why}
    return out


__all__ = ["Backend", "BackendResult", "Hints", "make", "available"]
