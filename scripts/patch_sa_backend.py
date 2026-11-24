import pathlib

p = pathlib.Path("/home/sutong/acc-thu-agent/backends/sa.py")
t = p.read_text()

old = """    def run(self, prob, ii, hints=None, workdir=None, timeout=3600.0, root=None,
            force_anneal=False, optimize=False, budget=0, max_ii=None):"""
new = """    def run(self, prob, ii, hints=None, workdir=None, timeout=3600.0, root=None,
            force_anneal=False, optimize=False, budget=0, max_ii=None,
            select=0, pick=0):"""
assert t.count(old) == 1, "sa.py: run 签名没找到"
t = t.replace(old, new)

old = """        if budget:
            extra.extend(["--sa_budget", str(int(budget))])"""
new = """        if budget:
            extra.extend(["--sa_budget", str(int(budget))])
        # M1.5: 邻域算子策略。0 = 上游（纯随机挑节点 / 洗牌取首个候选）。
        if select:
            extra.extend(["--sa_select", str(int(select))])
        if pick:
            extra.extend(["--sa_pick", str(int(pick))])"""
assert t.count(old) == 1, "sa.py: budget 分支没找到"
t = t.replace(old, new)

old = """                 "hints": hints.note or "","""
new = """                 "select": select, "pick": pick,
                 "hints": hints.note or "","""
assert t.count(old) == 1, "sa.py: raw 字典没找到"
t = t.replace(old, new)

p.write_text(t)
print("sa.py OK")
