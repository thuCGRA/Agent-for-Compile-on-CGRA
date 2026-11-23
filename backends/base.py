"""后端协议的统一定义。

一个后端就是"给定问题，产出一份放置"的函数。agent 不直接产出坐标
（唯一例外是 direct 后端里的小区域），它只决定**调用哪个后端、带什么参数**。

    Backend.run(prob, ii, hints=None) -> BackendResult

BackendResult.placement 是 {idx: (x, y, t)}，t 是模 II 的相位；
后端若只给绝对时刻，自己转。
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Hints:
    """agent 能施加的全部约束。空 Hints 等于"不管"。"""
    pins: dict = field(default_factory=dict)        # {idx: (x, y)} 或 {idx: (x, y, t)}
    forbidden: list = field(default_factory=list)   # [(idx, x, y), ...]
    region: list = field(default_factory=list)      # 只允许后端动这些节点
    budget: dict = field(default_factory=dict)      # 后端专属：rounds / temp / moves ...
    note: str = ""                                  # 给日志看的一句话
    # M1.5: 随机后端（SA）的种子。固定种子是"同实例同判据"对比的前提——
    # 上游 SA 每个随机点都用真随机源，同一输入两次跑出不同映射，
    # 甚至一次能映射一次不能。
    seed: int = 20136

    def is_empty(self):
        return not (self.pins or self.forbidden or self.region or self.budget)

    def to_constraints_json(self):
        """转成 mapper --constraints 读的格式；空约束返回 None。

        pins 里的 t（相位）会被丢掉：相位是 mapper 从数据到达时间推出来的结果，
        钉住它等于把 mapper 的搜索空间砍掉一半（旧工程的实测：全 pin 只有 2/10 可映射，
        只给 (x,y) 是 7/10）。这里只保留 agent 真正该管的空间维度。
        """
        if not self.pins and not self.forbidden:
            return None
        doc = {"pin_mode": "strict"}
        if self.pins:
            doc["pins"] = {str(int(i)): [int(p[0]), int(p[1])]
                           for i, p in self.pins.items()}
        if self.forbidden:
            f = {}
            for item in self.forbidden:
                idx, x, y = int(item[0]), int(item[1]), int(item[2])
                f.setdefault(str(idx), []).append([x, y])
            doc["forbidden"] = f
        return doc


@dataclass
class BackendResult:
    backend: str = ""
    ok: bool = False
    ii: int = 0
    placement: dict = field(default_factory=dict)   # idx -> (x, y, t) 相位
    lat: int = 0                                    # 绝对时刻的最大值
    elapsed: float = 0.0
    raw: dict = field(default_factory=dict)         # 后端自报的额外信息
    error: str = ""
    artifacts: dict = field(default_factory=dict)   # binary / workdir 等

    def as_dict(self):
        return {
            "backend": self.backend, "ok": self.ok, "ii": self.ii,
            "n_placed": len(self.placement), "lat": self.lat,
            "elapsed": self.elapsed, "error": self.error,
        }


class Backend:
    name = "base"

    def available(self):
        return True, ""

    def run(self, prob, ii, hints: Optional[Hints] = None, **kw):
        raise NotImplementedError
