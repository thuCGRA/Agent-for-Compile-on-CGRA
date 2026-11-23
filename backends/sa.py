"""SA 后端（mapper 的 `-m 1`）。

上游的 SA 有一个关键行为：`initMap()` 用贪心把节点按顺序 Route 一遍，
如果这一次就合法（`unmapped == 0 && overuse == 0`），**直接 return，一次都不退火**。
我们三个 kernel 的贪心初始化都一次成功，所以历史上横评里 SA 与 PathFinder
的 II/lat 逐项相同——看起来"SA 没用"，其实是它根本没跑。

M0 的任务就是把这条早退变成可关的开关（`force_anneal=True`），
让 SA 真的退火，之后才有"agent 加速 SA"可言。
"""
from .base import Backend, BackendResult, Hints
from tools import mapper as mapper_mod


class SABackend(Backend):
    name = "sa"

    def run(self, prob, ii, hints=None, workdir=None, timeout=3600.0, root=None,
            force_anneal=False, optimize=False, budget=0, max_ii=None):
        hints = hints or Hints()
        # M1.5: 种子必须显式传。上游 SA 用 std::random_device（真随机源）挑节点、
        # 洗候选表，同一输入两次跑出不同映射——不钉住种子，任何"agent vs SA"
        # 的对比都是在比较两个不同的搜索过程，实验结论没有意义。
        extra = ["--sa_seed", str(int(hints.seed))]
        if force_anneal or optimize:
            # optimize 隐含 force_anneal：热启动已可行时也必须进退火循环
            extra.append("--sa_force_anneal")
        if optimize:
            extra.append("--sa_optimize")
        if budget:
            extra.extend(["--sa_budget", str(int(budget))])
        run = mapper_mod.run_mapper(
            prob["kernel"], backend=self.name, ii=ii,
            xdim=prob["xdim"], ydim=prob["ydim"],
            constraints=hints.to_constraints_json(),
            workdir=workdir, timeout=timeout, root=root, extra_args=tuple(extra),
            real_nodes=[n["idx"] for n in prob["nodes"]],
            max_ii=max_ii,
        )
        res = BackendResult(
            backend=self.name, ok=bool(run.ok), ii=run.ii,
            lat=run.lat, elapsed=run.elapsed,
            artifacts={"workdir": run.workdir, "binary": run.binary},
            raw={"result_txt": run.result_txt, "force_anneal": force_anneal,
                 "optimize": optimize, "budget": budget, "maprun": run,
                 "hints": hints.note or "",
                 "constrained": bool(hints.pins or hints.forbidden)},
        )
        if not run.placement:
            res.error = "SA 没有产出 mappingwithlatency.txt"
            return res
        real = {n["idx"] for n in prob["nodes"]}
        mod = run.ii if run.ii else max(1, ii or 1)
        res.placement = {i: (x, y, t % mod)
                         for i, (x, y, t) in run.placement.items() if i in real}
        if len(res.placement) != len(real):
            res.ok = False
            res.error = "SA 只放了 %d/%d 个节点" % (len(res.placement), len(real))
        return res
