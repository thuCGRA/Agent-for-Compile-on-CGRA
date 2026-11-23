"""驱动 acc-thu 的 cgra_xml_mapper，并把它的产物读回成 Python 结构。

这一层不含任何决策：给什么参数就跑什么，跑完把 (x, y, t) 表交出来。
agent 的所有动作最终都落到这里的形参上。

产物命名（mapper 自己决定，别改）：
  <dfg><arch>_MTP=1_II=<ii>_Iter=<n>.mappingwithlatency.txt   PathFinder，逐行 idx,x,y,t
  <dfg><arch>_MTP=1_II=<ii>_SA.mappingwithlatency.txt         SA
  <dfg>_DP1_XDim=4_YDim=4_II=<ii>_MTP=1_binary.bin            bitstream，喂仿真器
  result.txt                                                   "<dfg> method:<Name> <ii> <iter>"
  map.log                                                      mapper 的 stdout
"""
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

BACKENDS = {
    "pathfinder": 0,
    "sa": 1,
    "lisa": 2,          # 上游有，但缺 GNN 模型，跑不通；留着是为了报错清楚
}

_II_RE = re.compile(r"_II=(\d+)")
_LAT_FILE_RE = re.compile(r"_II=(\d+)_(?:Iter=\d+|SA)\.mappingwithlatency\.txt$")
_RESULT_RE = re.compile(r"method:\s*(\w+)\s+(\d+)")


class MapperError(RuntimeError):
    pass


def acc_thu_root():
    return os.environ.get("ACC_THU_ROOT") or os.path.expanduser("~/acc-thu")


def mapper_binary(root=None):
    path = os.path.join(root or acc_thu_root(), "out", "cgra_xml_mapper")
    if not os.path.exists(path):
        raise MapperError("mapper 不存在: %s（先在 acc-thu 里跑 scripts/build.sh）" % path)
    return path


def kernel_dir(kernel, root=None):
    return os.path.join(root or acc_thu_root(), "out", kernel)


def _read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def parse_latency_file(path):
    """`idx,x,y,t` 每行一个节点。t 是绝对时刻，不是相位。

    注意：这个文件经常是 **0 字节**（mapper 只在某些分支才写），
    所以它只能当首选来源，不能当唯一来源。真值请用 routeInfo.log。
    """
    placement = {}
    lat = 0
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 4:
            continue
        try:
            idx, x, y, t = (int(p) for p in parts[:4])
        except ValueError:
            continue
        placement[idx] = (x, y, t)
        lat = max(lat, t)
    return placement, lat


# routeInfo.log 的行：
#   node=12,mapped=PE_MEM_X0_Y2-T0
#   node=15,mapped=PE_X1|_Y2|-T2      <- 普通 PE 的坐标里带 '|'
_MAPPED_RE = re.compile(
    r"^node=(\d+),mapped=PE(?:_MEM)?_X(\d+)\|?_Y(\d+)\|?-T(\d+)\s*$")


def parse_route_info(path):
    """从 routeInfo.log 抽出 {idx: (x, y, t_abs)}。

    这是唯一"每份成功映射都有、且带完整 (x,y,t)"的产物；
    mappingwithlatency.txt 常为 0 字节，mapping.csv 只有迭代过程。

    **注意 T 的含义**：routeInfo.log 里的 `-T<n>` 是**相位**（模 II），
    不是绝对时刻；mappingwithlatency.txt 的第 4 列才是绝对时刻。
    两者都能用 `t % ii` 归一成相位，所以下游不用区分，但 lat 只有前者能给。
    """
    placement = {}
    for line in _read_text(path).splitlines():
        m = _MAPPED_RE.match(line.strip())
        if not m:
            continue
        idx, x, y, t = (int(g) for g in m.groups())
        placement[idx] = (x, y, t)
    return placement, 0          # lat 未知，不要拿相位当 lat


def _route_logs(workdir):
    """所有 routeInfo.log，成功的排前面、覆盖节点多的排前面。"""
    out = []
    for name in os.listdir(workdir):
        if not name.endswith(".routeInfo.log"):
            continue
        path = os.path.join(workdir, name)
        text = _read_text(path)
        if not text:
            continue
        n_nodes = len(_MAPPED_RE.findall(text) and
                      [l for l in text.splitlines() if _MAPPED_RE.match(l.strip())])
        out.append((("Map Success" in text, n_nodes), path))
    out.sort(reverse=True)
    return [p for _k, p in out]


def extract_placement(workdir, ii=None, real_nodes=None):
    """把一次 mapper 运行的放置读回来。

    依次尝试 mappingwithlatency.txt（可能空）与 routeInfo.log，
    选"覆盖真实节点最多"的那份。mapper 会额外写 20019/20020 这类伪节点，
    real_nodes 给定时会把它们滤掉。
    """
    best = ({}, 0, None)
    candidates = []

    lat_file = find_placement_file(workdir, ii)
    if lat_file:
        candidates.append(("mappingwithlatency", lat_file, parse_latency_file))
    for path in _route_logs(workdir):
        candidates.append(("routeInfo", path, parse_route_info))

    for kind, path, parser in candidates:
        placement, lat = parser(path)
        if real_nodes:
            placement = {i: v for i, v in placement.items() if i in real_nodes}
        score = (len(placement), lat)
        if score > (len(best[0]), best[1]):
            best = (placement, lat, "%s:%s" % (kind, os.path.basename(path)))
    return best


def find_placement_file(workdir, ii=None):
    """挑最新的、非空的 mappingwithlatency.txt（SA 与 PathFinder 命名不同）。"""
    best = None
    for name in os.listdir(workdir):
        if not name.endswith(".mappingwithlatency.txt"):
            continue
        path = os.path.join(workdir, name)
        if os.path.getsize(path) == 0:
            continue
        if ii is not None:
            m = _II_RE.search(name)
            if m and int(m.group(1)) != int(ii):
                continue
        mtime = os.path.getmtime(path)
        if best is None or mtime > best[0]:
            best = (mtime, path)
    return best[1] if best else None


def find_binary(workdir, ii=None):
    cands = [n for n in os.listdir(workdir) if n.endswith("_binary.bin")]
    if ii is not None:
        keep = [n for n in cands if ("_II=%d_" % ii) in n]
        cands = keep or cands
    if not cands:
        return None
    cands.sort(key=lambda n: os.path.getmtime(os.path.join(workdir, n)))
    return os.path.join(workdir, cands[-1])


@dataclass
class MapRun:
    kernel: str
    backend: str
    ii_requested: int
    ii: int = 0
    ok: bool = False
    lat: int = 0
    placement: dict = field(default_factory=dict)      # idx -> (x, y, t) 绝对时刻
    workdir: str = ""
    elapsed: float = 0.0
    stdout: str = ""
    binary: Optional[str] = None
    result_txt: str = ""
    source: str = ""
    error: str = ""
    success_text: bool = False

    def phases(self):
        """转成 core.check 要的相位表：{idx: (x, y, t % ii)}。"""
        if not self.ii:
            return {}
        return {i: (x, y, t % self.ii) for i, (x, y, t) in self.placement.items()}


def run_mapper(kernel, backend="pathfinder", ii=0, xdim=4, ydim=4,
               constraints=None,
               workdir=None, timeout=1800.0, extra_args=(), root=None,
               keep=False, real_nodes=None, max_ii=None):
    """在独立工作目录里跑一次 mapper。

    workdir 为 None 时用 work/map/<kernel>/<backend>_ii<ii>/。
    keep=True 才复用目录（调试用）；默认每次清空，避免读到上一次的产物。
    返回 MapRun（不抛异常，除非二进制缺失）。
    """
    root = root or acc_thu_root()
    if backend not in BACKENDS:
        raise MapperError("未知后端 %r，可选 %s" % (backend, sorted(BACKENDS)))

    kdir = kernel_dir(kernel, root)
    dfg = os.path.join(kdir, "dfg.xml")
    arch = os.path.join(kdir, "arch.json")
    for p in (dfg, arch):
        if not os.path.exists(p):
            raise MapperError("缺 %s（先跑 acc-thu 的 gen_dfg.sh + inject_mem.sh）" % p)

    if workdir is None:
        workdir = os.path.join(root, "work", "map", kernel, "%s_ii%d" % (backend, ii))
    # 每次跑都清空：mapper 的 result.txt 是 std::ios_base::app（跨次累加），
    # 旧的 *_binary.bin / *_mappingwithlatency.txt / *_routeInfo.log 也会留着。
    # 不清就会读到上一次的产物 —— 这正是旧工程里"仿真 abort 结论不可复现"的来源。
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir, exist_ok=True)

    # mapper 会往 cwd 里写一堆文件，输入用软链指过去（与 scripts/map.sh 一致）
    for name, src in (("dfg.xml", dfg), ("arch.json", arch)):
        dst = os.path.join(workdir, name)
        if os.path.islink(dst) or os.path.exists(dst):
            os.remove(dst)
        os.symlink(src, dst)

    cmd = [mapper_binary(root), "-d", "dfg.xml", "-j", "arch.json",
           "-x", str(xdim), "-y", str(ydim), "-i", str(ii),
           "-t", "HyCUBE_4REG", "-m", str(BACKENDS[backend])]

    # M1: 约束走文件。写成绝对路径再交给 mapper——它的 cwd 是 workdir，
    # 相对路径会解析到别处（旧工程踩过：找不到文件直接 SIGSEGV）。
    if constraints:
        cpath = os.path.join(workdir, "constraints.json")
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(constraints, f, indent=1, sort_keys=True)
        cmd.extend(["--constraints", cpath])
    if max_ii:
        # mapper 的 II 循环是 `II++; if (II == max_II) break;`（等号！），
        # 所以传进去的必须是"第一个放弃的 II"，这里统一 +1。
        # 只影响"失败后要不要继续往上试 II"，不影响成功时的搜索。
        cmd.extend(["--max_II", str(int(max_ii) + 1)])
    cmd.extend(extra_args)

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=workdir, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
        out = proc.stdout.decode("utf-8", "replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") + "\n[mapper 超时 %ss]" % timeout
        rc = -9
    elapsed = time.time() - t0

    run = MapRun(kernel=kernel, backend=backend, ii_requested=int(ii),
                 workdir=workdir, elapsed=round(elapsed, 3),
                 stdout=out[-4000:])
    with open(os.path.join(workdir, "map.stdout.log"), "w", encoding="utf-8") as f:
        f.write(out)

    run.result_txt = _read_text(os.path.join(workdir, "result.txt")).strip()
    # result.txt 用 app 模式追加，可能有多行；取最后一行的匹配
    m = None
    for m in _RESULT_RE.finditer(run.result_txt):
        pass
    if m:
        run.ii = int(m.group(2))
    if not run.ii:
        # 退回从文件名里捞
        for name in os.listdir(workdir):
            mm = _II_RE.search(name)
            if mm:
                run.ii = max(run.ii, int(mm.group(1)))

    run.success_text = "Map Success" in out
    # 只认 DFG 里的真实节点（mapper 会写 20019/20020 这类伪节点）
    real = set(int(i) for i in real_nodes) if real_nodes else None
    placement, lat, source = extract_placement(workdir, run.ii or None, real)
    if placement:
        run.placement = placement
        run.lat = lat
        run.source = source
        run.binary = find_binary(workdir, run.ii or None)
        # 三条同时成立才算成功（见文件头注释：失败也会留半截产物、退出码恒 0）
        problems = []
        if not run.success_text:
            problems.append("stdout 里没有 'Map Success'（mapper 实际失败）")
        if real and len(placement) < len(real):
            problems.append("只覆盖 %d/%d 个节点（半截产物）"
                            % (len(placement), len(real)))
        if problems:
            run.ok = False
            run.error = "; ".join(problems)
        else:
            run.ok = True
    else:
        run.error = "mapper 没有可读的放置产物（mappingwithlatency 与 routeInfo.log 都是空/缺）"
    return run
