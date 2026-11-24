import pathlib

base = pathlib.Path("/home/sutong/acc-thu-agent/backends/base.py")
sa = pathlib.Path("/home/sutong/acc-thu-agent/backends/sa.py")

def sub(text, old, new, tag):
    assert text.count(old) == 1, "%s: %d 处" % (tag, text.count(old))
    return text.replace(old, new)

# ---------- base.py: Hints 加 seed ----------
t = base.read_text()
t = sub(
    t,
    '    note: str = ""                                  # 给日志看的一句话',
    '    note: str = ""                                  # 给日志看的一句话\n'
    '    # M1.5: 随机后端（SA）的种子。固定种子是"同实例同判据"对比的前提——\n'
    '    # 上游 SA 每个随机点都用真随机源，同一输入两次跑出不同映射，\n'
    '    # 甚至一次能映射一次不能。\n'
    '    seed: int = 20136',
    "Hints.seed")
base.write_text(t)

# ---------- sa.py: 传 --sa_seed ----------
t = sa.read_text()
t = sub(
    t,
    "        hints = hints or Hints()\n        extra = []\n",
    "        hints = hints or Hints()\n"
    "        # M1.5: 种子必须显式传。上游 SA 用 std::random_device（真随机源）挑节点、\n"
    "        # 洗候选表，同一输入两次跑出不同映射——不钉住种子，任何\"agent vs SA\"\n"
    "        # 的对比都是在比较两个不同的搜索过程，实验结论没有意义。\n"
    "        extra = [\"--sa_seed\", str(int(hints.seed))]\n",
    "sa.py seed arg")
sa.write_text(t)
print("OK: base.py 加 seed 字段；sa.py 传 --sa_seed")
