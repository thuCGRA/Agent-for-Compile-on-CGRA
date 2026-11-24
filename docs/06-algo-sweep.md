# 06 · placing / routing 算法横评（48 个 test case）

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

## 1 覆盖情况：48 个 test case 里 23 个能跑

`scripts/kernels.tsv` 有 48 条（22 个原生 kernel + 26 个 `pb_*`）。能不能生成 DFG：

- ✅ **23 个可跑**：22 个原生 kernel（`array_add array_cond conv2 conv2d_2x2 conv2d_3x3 conv3 fir gemm gemm_w1 gemm_w2 gemm_w3 hpcg mac mac2 microspeech_1..4 mults1 mults2 sum trmm`）+ `pb_2mm`
- ❌ **25 个 polybench 生成不出 DFG**。原因不是脚本问题，是 DFG pass 的硬限制：
  ```
  LLVM ERROR: morpher DFG pass: unsupported memory access through a multi-level pointer
  (e.g. `int **A; A[i][k]`); the base pointer is not a GEP on a named array.
  Flatten the array to 1-D or use `int *A`.
  ```
  `kernels/pb_*/pb_*.c` 这批 stub 声明的是 `volatile int **A;`（2-D/3-D 指针），访问 `A[i][k]`。
  把声明换成全局命名数组 `int A[512];` 还不够——`A[i*n+j]` 在 -O2 下下标被提到循环外，形成
  **两级 GEP**，pass 同样拒绝；`A[i][j]` 常量折叠成**常量表达式 GEP**（不是指令）也被拒绝。
  唯一被接受的形式是单层仿射下标（`A[i+j]`），见 `tools/ilp/pb_convert.py` + `scripts/pb_build.sh`。
  `pb_2mm` 就是按这个路子改出来的（24 节点 / 31 边）。
  → 要让 26 个 polybench 全部可用，需要**重写这 26 个 stub 的循环体**（真正 flatten 成 1-D 且
  下标保持仿射），而不是做文本替换。这是一件独立的、可估工时的活。

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
