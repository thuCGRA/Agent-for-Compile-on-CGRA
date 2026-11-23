"""PathFinder 后端（mapper 的 `-m 0`，A* + 协商式拥塞）。

它是目前唯一"能跑、且产物能过仿真"的后端，所以所有对照实验都以它为基线。
"""
from .base import Backend, BackendResult, Hints
from tools import mapper as mapper_mod


class PathFinderBackend(Backend):
    name = "pathfinder"

    def run(self, prob, ii, hints=None, workdir=None, timeout=1800.0, root=None):
        hints = hints or Hints()
        run = mapper_mod.run_mapper(
            prob["kernel"], backend=self.name, ii=ii,
            xdim=prob["xdim"], ydim=prob["ydim"],
            workdir=workdir, timeout=timeout, root=root,
        )
        res = BackendResult(
            backend=self.name, ok=bool(run.ok), ii=run.ii,
            lat=run.lat, elapsed=run.elapsed,
            artifacts={"workdir": run.workdir, "binary": run.binary},
            raw={"result_txt": run.result_txt, "maprun": run},
        )
        if not run.placement:
            res.error = "mapper 没有产出 mappingwithlatency.txt"
            return res
        # 只保留 DFG 里真实存在的节点（mapper 会额外写 20019/20020 这类伪节点）
        real = {n["idx"] for n in prob["nodes"]}
        res.placement = {i: (x, y, t % run.ii)
                         for i, (x, y, t) in run.placement.items() if i in real}
        if len(res.placement) != len(real):
            res.ok = False
            res.error = "mapper 只放了 %d/%d 个节点" % (len(res.placement), len(real))
        return res
