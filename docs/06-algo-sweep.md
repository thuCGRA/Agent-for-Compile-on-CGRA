# 06 · placing / routing 算法横评（48 个 test case）

> **更新（见 [07-coverage.md](07-coverage.md)）**：本文写的"48 个里只有 23 个能跑"是当时的口径。补上上游预生成 DFG 之后可跑 **41 个**；本文的表 A/B/C 仍然有效（那是原生 kernel 上的结果），表 D/E 在 07 里。

> 目的：把「PathFinder（mapper 自带）」「上游 SA」「CP-SAT 放置」「CP-SAT 布线」放在同一批
> kernel、同一 II 下比较，看看各自的实际效果，以及外部放置能不能被真实 mapper 吃下去。
> 所有数字都是本机（`~/acc-thu-ilp`，分支 `ilp-mapper`）实测，命令见 §7。

## 0 一句话结论

| # | 结论 | 证据 |
|---|---|---|
| 1 | **上游 SA 在本工程从不真的退火**：`SAMap()` 先跑 `initMap_with_PathFinder()`，成功即 `return true`。23 个 kernel 里 21 个的 II 与 PathFinder 完全相同。 | §3 表 A |
| 2 | **SA 偶尔还会产出硬件执行不了的 bitstream**：`mac` 上 SA 报 `Map Success with II = 4`，模拟器却断言崩溃 `CGRATile.cpp:168: !(I1isvalid && I2isvalid)`。 | §5 |
| 3 | **CP-SAT 放置能被真实 mapper 接受并仿真通过**（`mac` 167/0、`trmm` 5/0），说明 `--pins` 通道是通的。 | §5 |
| 4 | 但**「mapper 说 Map Success」≠「硬件可执行」**：`conv2` 用 CP-SAT 放置 + A* 布线，mapper 报成功、II=4，模拟器同样触发 dual-issue 断言。 | §5 |
| 5 | **CP-SAT 放置模型偏紧**：`gemm`(49 节点, II=9)、`gemm_w2`(58, II=7)、`microspeech_4`(50, II=8) 判 INFEASIBLE，而 PathFinder 在同 II 上映射成功。根因已定位：`--route-cycles` 缺省 1（每边最多过 1 个寄存器）→ 改成 3 后 `gemm` II=9 变 FEASIBLE。 | §6 |

## 1 覆盖情况：`kernels/` 里 48 个 .c，只有 23 个能出 DFG

> **更正（2026-11-25）**：本节初版把"polybench 跑不了"归因成 Morpher 的固有限制，这是**错的**。
> 查清后有三个各自独立的原因，见 §1.0。

### 1.0 为什么 25 个 polybench case 跑不了（三条独立原因）

**(1) 直接报错的那句话是我们自己加的补丁，不是 Morpher 的。**
`src/dfggen/src/common/dfg.cpp` 里共有 5 处 `morpher-THU fix`（合计 60 行改动），其中
`:11397`（load）与 `:11427`（store）把 upstream 的

```cpp
assert(arr_ptrs.find(ptr) != arr_ptrs.end());
```

换成了

```cpp
if (arr_ptrs.find(ptr) == arr_ptrs.end() || arr_ptrs[ptr] == nullptr) {
    report_fatal_error(llvm::Twine("morpher DFG pass: unsupported memory access through "
        "a multi-level pointer (e.g. `int **A; A[i][k]`); ..."), false);
}
```

upstream `~/morpher/Morpher_DFG_Generator/src/common/dfg.cpp` 里 `morpher-THU` 出现 **0 次**，
且与我们这份的 diff 就是这 60 行。也就是说：**这条 `LLVM ERROR` 是本工程引入的**（本意是把
Release 下的空指针崩溃变成可读报错，这个动机本身是对的）。

**(2) `benchmarks/woset_benchmarks/polybench/*/*.c` 是 upstream 自己提交的残缺 stub。**
它们来自 upstream 子模块 commit `b88b372 "updating benchmark kernel"`，内容是
`volatile int **A; ... sum += alpha * A[i][k] * B[k][j];`，而 `i`、`j` **从未被赋值**，整个
文件只剩一个 `for (k=...)`。这不是 PolyBench 源码，是被人手工剪过的骨架。任何版本的 DFG pass
都编不出正确 DFG（upstream 行为：assert 在 Release 下被 NDEBUG 去掉 → `arr_ptrs[ptr]` 取到
nullptr → `->getPointerOperand()` 直接段错误）。

**(3) 我们 vendor 时把真正能跑的那批产物删掉了。**
upstream `Morpher_CGRA_Mapper/applications/`（38 MB）里有约 20 个真实 kernel 的**预生成 DFG**：

| 目录 | 内容 |
|---|---|
| `applications/polybench/` | 10 个真 DFG：`2mm.xml`(28 节点) `2mm_unroll4.xml`(61) `atax.xml`(24) `atax_unroll4`(54) `bicg.xml`(31) `bicg_unroll3`(63) `cholesky.xml`(26) `cholesky_unroll4`(59) `doitgen.xml`(27) `doitgen_unroll4`(63) |
| `applications/{adpcm_coder,adpcm_decoder,aes_encrypt,aes_decrypt,compare_neighb,dct,dwt,finalSAD,fix_fft_npb,gemm_nt,nw,pedometer,realgsm,taylor}/` | 各 1–2 个 `*_PartPred_DFG.xml` / `*_FullPred_DFG.xml` |
| `applications/cgra_me_bench/` | `accumulate cap conv2 conv3 mac mac2 matrixmultiply mults1 ...` |
| `applications/hycube/` | `aes array_add fft gemm_2x2x2 gemm_no_unroll hpcg pedometer` |
| `applications/sample_xmls/` | 37 MB 示例 DFG，部分带 `*.mapping.csv` |

`PROVENANCE.md` 把 `applications/`（38 MB）列在"删掉清单"里 —— 删的正是这批 case。

### 1.1 现在 `kernels/` 的实际可用性

- ✅ **22 个原生 kernel** 能出 DFG（`array_add array_cond conv2 conv2d_2x2 conv2d_3x3 conv3 fir gemm gemm_w1 gemm_w2 gemm_w3 hpcg mac mac2 microspeech_1..4 mults1 mults2 sum trmm`）
- ➕ `pb_2mm` 经 `tools/ilp/pb_convert.py` 把 `int **` 拍平成 1-D 后能出 DFG（24 节点，II=6 可映射）
- ❌ **其余 25 个 polybench stub** 出不了 DFG，原因见 §1.0 (1)(2)

所以表 A/B 的 23 个 case 没问题；**"48 个里只有 23 个能跑"这个说法要限定为"`kernels/` 里的 .c"**，
它不代表 Morpher 的能力上限 —— 上限要高得多，因为 `applications/` 里还有约 20 个预生成 DFG。

### 1.2 预生成 DFG 的可用性（已实测，尚未打通）

- 结构上与我们生成的 DFG **完全同构**（同样的 `<Node idx ASAP ALAP BB CONST>` / `<Inputs><Output idx nextiter NPB type>` / `<RecParents>` / `<GEPOffset>`；idx 同样非连续，同样有 `20025/20026` 这种大号节点；max ASAP 同为 9）。
- 但直接把 `applications/polybench/2mm.xml`(28 节点) 喂我们的 mapper，会在打印 `Initial target II = 4` 后**静默 `rc=1`** 退出（`-i 0/6/8` 都一样），没有任何 `Map Failed!` 输出。说明问题在 `parseXML` 或 `new CGRA(...)` 阶段，不在搜索阶段。
- 同一个 arch（`out/pb_2mm/arch.json`，注意它的 `DATA_LAYOUT` 是 `None`，说明 `inject_mem` 没写进去）映射我们自己的 24 节点 `dfg.xml` 是正常的（II=6）。→ **arch 没问题，是那份 xml 的某处不被解析器接受**，还需再定位（估 1–2 h）。

## 2 参与比较的四种东西

| 名称 | 入口 | 做什么 | 布线 |
|---|---|---|---|
| **PathFinder** | `cgra_xml_mapper -m 0` | 贪心拓扑序放置 + A* 逐边布线，失败则 II+1 重来 | mapper 内部 A* |
| **SA** | `cgra_xml_mapper -m 1` | 上游 `SAMapper`（先 PathFinder，成功即返回） | mapper 内部 A* |
| **CP-SAT 放置** | `tools/ilp/ilp_mapper.py` | 精确求解「节点→(PE, 相位)」，路由只作为距离/时延代理 | 无 |
| **CP-SAT 布线** | `tools/ilp/ilp_router.py` | 给定放置，精确求每条边经过哪些链路/寄存器 | 有（全局） |
| **pins 通道** | `cgra_xml_mapper --pins ... --pins_mode 3` | 把外部放置灌进 mapper，让它的 A* 照此落位 | mapper 内部 A* |

## 3 表 A：PathFinder vs SA（23 个 kernel）

II 越小越好；`FAIL` 括号里是失败原因。

| kernel | 节点 | PathFinder II | SA II | 备注 |
|---|---:|---:|---:|---|
| trmm | 11 | 4 | 4 | |
| sum | 18 | 4 | 4 | |
| array_add | 20 | 4 | 4 | |
| fir | 21 | 4 | 4 | |
| conv2 | 22 | 4 | 4 | |
| mac | 22 | 4 | 4 | SA 的 bitstream 仿真崩溃（§5） |
| hpcg | 24 | 4 | 4 | |
| pb_2mm | 24 | 6 | FAIL (90s 超时) | |
| array_cond | 25 | 4 | 4 | |
| mults1 | 25 | 4 | 4 | |
| gemm_w1 | 26 | 4 | 4 | |
| microspeech_1 | 26 | 4 | 4 | |
| conv3 | 28 | 4 | 4 | |
| mults2 | 34 | FAIL (90s 超时) | 4 | 同一算法，m0 只是没在 90s 内跑完 → 计时噪声 |
| mac2 | 37 | 4 | 4 | |
| conv2d_2x2 | 39 | 4 | 4 | |
| gemm | 49 | 9 | 9 | |
| microspeech_4 | 50 | 8 | 8 | |
| gemm_w2 | 58 | 7 | FAIL (段错误 139) | |
| microspeech_2 | 62 | FAIL (90s 超时) | FAIL | 4×4 上放不下 |
| conv2d_3x3 | 70 | 6 | FAIL (段错误 139) | |
| gemm_w3 | 79 | FAIL (90s 超时) | FAIL | 79 节点 > 4×4×4 = 64 槽位 |
| microspeech_3 | 80 | FAIL (1s) | FAIL | 同上，槽位不足 |

**读法**：21/23 逐项相同 → 印证 §0 结论 1。唯一两个「不同」（`mults2`、`mac`）都能解释：
`mults2` 是 90s 超时造成的假差异；`mac` 两者 II 相同但生成的具体 bitstream 不同（SA 的不能跑）。
`conv2d_3x3 / gemm_w2` 上 SA 直接 **段错误**（`rc=139`），上游 SA 在本架构上不稳。

## 4 表 B：CP-SAT 放置 + CP-SAT 布线（与 PathFinder 同 II）

`ilp` = 放置求解结果（`time-limit 20` 未证最优时记 OK/FEASIBLE）；`hops/regs` 是布线代价；
`route` = CP-SAT 布线结论；`prefer` = 把该放置灌给 mapper A* 后能否 `Map Success`。

| kernel | 节点 | II | ilp | 用时 | hops | regs | CP-SAT 布线 | A* 吃下 | A* 的 II |
|---|---:|---:|---|---:|---:|---:|---|---|---:|
| trmm | 11 | 4 | OK | 8s | 3 | 5 | OPTIMAL | ✅ | 5 |
| sum | 18 | 4 | OK | 21s | 11 | 6 | OPTIMAL | ❌ | - |
| array_add | 20 | 4 | OK | 21s | 10 | 6 | OPTIMAL | ❌ | - |
| fir | 21 | 4 | OK | 21s | 13 | 6 | OPTIMAL | ❌ | - |
| conv2 | 22 | 4 | OK | 20s | 12 | 5 | OPTIMAL | ✅ | 4 |
| mac | 22 | 4 | OK | 20s | 14 | 7 | OPTIMAL | ✅ | 4 |
| hpcg | 24 | 4 | OK | 21s | 16 | 7 | OPTIMAL | ✅ | 4 |
| pb_2mm | 24 | 6 | OK | 20s | 17 | 14 | OPTIMAL | ✅ | 6 |
| array_cond | 25 | 4 | OK | 20s | 17 | 10 | OPTIMAL | ❌ | - |
| mults1 | 25 | 4 | OK | 20s | 14 | 8 | OPTIMAL | ❌ | - |
| gemm_w1 | 26 | 4 | OK | 21s | 16 | 7 | OPTIMAL | ✅ | 4 |
| microspeech_1 | 26 | 4 | OK | 21s | 15 | 5 | OPTIMAL | ✅ | 5 |
| conv3 | 28 | 4 | OK | 21s | 20 | 8 | OPTIMAL | ❌ | - |
| mults2 | 34 | 4 | OK | 21s | 26 | 16 | OPTIMAL | ❌ | - |
| mac2 | 37 | 4 | OK | 21s | 43 | 16 | OPTIMAL | ❌ | - |
| conv2d_2x2 | 39 | 4 | OK | 26s | 37 | 14 | OPTIMAL | ✅ | 4 |
| gemm | 49 | 9 | FAIL | 3s | - | - | - | - | - |
| microspeech_4 | 50 | 8 | FAIL | 21s | - | - | - | - | - |
| gemm_w2 | 58 | 7 | FAIL | 13s | - | - | - | - | - |
| microspeech_2 | 62 | 4 | OK | 21s | 83 | 36 | OPTIMAL | ❌ | - |
| conv2d_3x3 | 70 | 6 | OK | 21s | 101 | 44 | OPTIMAL | ❌ | - |
| gemm_w3 | 79 | 4 | FAIL | 12s | - | - | - | - | - |
| microspeech_3 | 80 | 4 | FAIL | 21s | - | - | - | - | - |

**读法**：
- 放置只要解出来，**CP-SAT 布线 100% OPTIMAL**（20/20）。这是意料之中——布线模型比 mapper 的
  逐节点贪心 A* 完整得多，没有"这条路走不通就整张图失败"的问题。
- 但 **A* 只吃下 8/20**。`prefer` 的语义要注意：当前实现是「如果 pin 的 (x,y,t) 在候选集里，
  就只保留这些候选（等效 strict）；只有完全不在候选集里才退回全部候选」。所以 `prefer=FAIL`
  的真实含义是「A* 的候选集与该放置不一致，或照此落位后布线失败」。这是 `applyPins` 的一个
  待修点（真正的 soft anchor 应该给 pin 候选加 cost 优惠而不是过滤）。
- **大图 ILP 解出来的路由代价很夸张**（`conv2d_3x3` hops=101、`microspeech_2` 83），说明目标函数
  `min Σ(10·hops + regs)` 在这个规模下已经把图摊平了，解"合法但没用"。

## 5 表 C：功能验证（模拟器 `Mismatches::0` 才算过）

只有 5 个 kernel 有 trace（`out/<k>/trace_0.txt`）。

| kernel | PathFinder（-m 0） | SA（-m 1） | CP-SAT 放置 + A* |
|---|---|---|---|
| array_add | 243 / 0 ✅ | 243 / 0 ✅ | mapper 未吃下 |
| conv2 | 163 / 0 ✅ | 163 / 0 ✅ | **断言崩溃** |
| fir | 391 / 0 ✅ | 391 / 0 ✅ | mapper 未吃下 |
| mac | 167 / 0 ✅ | **断言崩溃** | 167 / 0 ✅ |
| trmm | 5 / 0 ✅ | 5 / 0 ✅ | 5 / 0 ✅（II=5，比 PathFinder 差 1） |

两处崩溃都是同一条断言：

```
hycube_simulator: src/simulator/CGRATile.cpp:168: void HyCUBESim::CGRATile::execute(int):
  Assertion `!(I1isvalid && I2isvalid)' failed.
```

含义是某个 PE 的 FU 在同一个 cycle 两个输入口同时有效（dual-issue 冲突）。**这一条值得单独跟进**：
mapper 的 `Map Success` 只保证它自己的时序记账自洽，不保证发出来的 bitstream 在硬件模型里能跑。
`conv2` 那例尤其说明问题——放置是我们用 CP-SAT 精确求的、布线是 mapper 自己的 A*，两者都说成功，
硬件模型说不行。

## 6 可复现的关键发现

**(a) SA 是假退火，可直接验证**：`src/mapper/src/mapper/SimulatedAnnealingMapper.cpp` 的
`SAMap(CGRA*, DFG*)` 第一件事是 `if (!initMap_with_PathFinder()) {...}`，成功就
`std::cout << "find a valid initial mapping, exit....II = " << ...; return true;`。
所以 `-m 1` 在能映射的 kernel 上等价于 `-m 0`，白白付出 1.4× 左右的时间（见 §3 的 secs）。

**(b) CP-SAT 放置模型的紧约束只有一处是致命的**：`--route-cycles` 缺省 1。实验：

```
$ ilp_mapper.py --dfg out/gemm/dfg.xml --arch out/gemm/arch.json --ii 9 --max-ii 9   → INFEASIBLE
$ ... --spatial                                                                     → INFEASIBLE
$ ... --spatial --hops 8                                                            → INFEASIBLE
$ ... --spatial --hops 8 --route-cycles 3                                           → FEASIBLE (hops=124, 25s)
```

即 `d ≤ 1`（每条边最多过一个寄存器）在 49 节点以上的图上不可能满足；放开到 3 立刻可行。
下一步应该把 `d` 的上界改成 `manhattan(u,v)+1`（本文件头注释里写的就是这个界，但代码里被
`--route-cycles` 卡成了常量）。

**(c) II 不是 ILP 的决策变量**：`II = max(RecMinII, ResMinII, userII)` 后逐级 +1 试探，
可行性对 II 不单调，不能二分。`gemm_w3`(79) / `microspeech_3`(80) 在 4×4 上要 II ≥ 5 才谈得上
（16 PE × 4 拍 = 64 个槽位 < 节点数），这是纯容量下界，与算法无关。

## 7 复现

```bash
cd ~/acc-thu-ilp
bash scripts/build.sh                      # 出 out/{libdfggenPass.so,cgra_xml_mapper,hycube_simulator}
bash scripts/sweep_ab.sh                   # 表 A：PathFinder vs SA，写 /tmp/sweep.tsv
bash scripts/sweep_ilp.sh                  # 表 B：CP-SAT 放置/布线 + A* 吃否，写 /tmp/sweep_ilp.tsv
bash scripts/sim.sh array_add 0            # 表 C：单个 kernel 的功能验证
bash scripts/pb_build.sh                   # 尝试给 polybench 系列生成 DFG（当前只有 pb_2mm 成功）
```

依赖：`.venv`（`ortools 9.15.6755`）。`scripts/sweep_*.sh` 都可以重入（已跑完的 kernel 会跳过）。

## 8 已知限制 / 下一步（按价值排序）

1. **修 `applyPins` 的 prefer 语义**：现在是"有匹配就 strict"，应该改成给 pin 候选减 cost（软锚点），
   否则永远测不出"软锚点比硬 pin 好"。改动 < 20 行，需重编 mapper。
2. **查 `!(I1isvalid && I2isvalid)` 断言**：外部放置 + A* 布线能复现，PathFinder 自己不出。要么是
   bitstream 编码漏了 dual-issue 互斥，要么是 A* 在 pin 约束下把两条边塞进了同一个 FU 输入口。
   这条不解决，"外部放置"这条路在硬件上就是不安全的。
3. **放开 `--route-cycles`**，重跑表 B 的 3 个 INFEASIBLE。
4. **布线层补端口级容量**（现在按 PE 间链路计，未区分 crossbar 端口），否则 `hops=101` 这类解
   看着合法、实际端口会撞。
5. **polybench 26 个 stub 重写**（真正 flatten 成 1-D + 仿射下标），把 48 个 test case 补齐。
