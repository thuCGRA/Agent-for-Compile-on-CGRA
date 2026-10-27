# 臂 B spike：mapper verify 模式

结论：**go**。`cgra_xml_mapper --verify` 现在能吃下"整张放置 + 每条边的 PE 坐标序列"，把它落到端口图上、打印二进制、写 JSON 报告；非法输入给出按规则分类的具体冲突。臂 B 的 `SAPlacer` 只需产出 `pins.json`，`LLMRouter` 只需产出 `routes.json`，两者都由同一套映射/打印代码校验。

## 1. 接口

```
out/cgra_xml_mapper -d <dfg.xml> -x 4 -y 4 -j <mem.json> -i 2 \
    -t HyCUBE_4REG -m 3 --pins pins.json --routes routes.json \
    --report report.json --verify
```

`pins.json`：

```json
{"pins": [{"node": 5, "x": 0, "y": 3, "t": 3, "lat": 11}]}
```

`node` 是 DFG 节点下标，`x/y` 是 PE 坐标，`t` 是 PE 内的 DP 槽位（0..3），`lat` 是该节点在 II 网格里的起始周期。缺 `lat` 时按最早可放置周期补。

`routes.json`：

```json
{"routes": [{"from": 11, "to": 12, "path": [[0, 1], [1, 1]]}]}
```

`path` 是这条边走过的 PE 坐标序列，含起点和终点 PE；不给 `path` 的边由内部 A* 补，所以"只给关键边"也能跑出完整映射。

`report.json`：

| 字段 | 含义 |
|---|---|
| `ok` / `mode` / `stage` | 是否通过；`verify`；失败发生在 `verify` 还是 `done` |
| `pins_total` / `pins_applied` / `pins_rejected` | 放置 pin 的接受情况 |
| `routes_total` / `routes_applied` / `routes_rejected` | 外部路线的接受情况 |
| `placement[]` | 实际落地的 `node/op/x/y/t/lat` |
| `ii` / `lat` | 本次映射的 II 与总延迟 |
| `conflicts[]` | `type`（`placement`/`timing`/`route`/`port`）+ `msg`，另带 `pe`、`cycle`、`port`、`edges` |

## 2. 校验规则

放置（pass 1）：

- 每个 DFG 节点都必须有 pin，且 pin 的 `(x,y,t)` 能放这个 op（`supportedOPs`）；同一 `(x,y,t)` 不能放两个节点。
- 下界：`earliest = max(父节点 lat + 父 op 延迟)`。**不加跳数项**——HyCUBE 在一拍内组合转发，跨 PE 不必然多花一拍，跳数不是相位下界（见 §4.1）。晚于 `earliest` 是允许的。

布线（pass 2）：先落外部给的 `path`，再给剩下的边跑 A*。每条外部 `path` 逐条校验：

- 相邻两个坐标必须是单步相邻（曼哈顿距离 1），否则 `route: route hop i is not a single PE step`。
- 首尾必须等于该边两端节点被 pin 的 PE，否则 `route: route path ends at (x,y) but the destination ...`。
- 每跳相位只能保持或 +1，且不得晚于消费周期（跨迭代的回边允许 +II），否则 `route: ... over N hop(s)`。
- 端口占用：同一个 `(PE, cycle, port)` 不能承载两个不同源节点的值（广播同一源到多个目的端不算冲突），否则 `port: port carries the values of nodes A B in the same cycle`。
- 寄存器等待：一条路径最多经过 2 个寄存器端口（`REG_HOP_CAP`）。二进制打印器只能编码有限的寄存器链，更长的等待即使映射通过也会在打印阶段崩掉，所以在搜索阶段就拒掉。

## 3. 实测（`array_add`，II=4，HyCUBE_4REG，4×4，MTP=1）

| 用例 | 输入 | 结果 | 二进制 | 仿真 |
|---|---|---|---|---|
| exp1 | 只给 20 个 pin | ok，routes 0/0 | `2efd6260c222`（5943 B） | Matches::243, Mismatches::0 |
| exp2 | + 参考路线 11→12 `[(0,1),(1,1)]` | ok，routes 1/1 | 与 exp1 逐字节相同 | Mismatches::0 |
| exp3 | + 绕行 11→12 `[(0,1),(0,2),(1,2),(1,1)]`（3 跳、0 相位余量） | ok，routes 1/1 | `1385e222aada`，与 exp1 不同 | Mismatches::0 |
| neg1 | 0→16 第二跳跨 2 格 | 拒：`route hop 0 is not a single PE step (2 + 2 != 1)` | — | — |
| neg2 | 11→12 停错 PE `[(0,1),(0,2)]` | 拒：`route path ends at (0,2) but the destination of node 12 is pinned to (1,1)` | — | — |
| neg3 | 12 的 pin 改到 cycle 0 | 拒：`node 12 is pinned to cycle 0 but its parents need it no earlier than 2` | — | — |
| neg4 | 4→5 绕西侧 5 跳 `[(1,0),(0,0),(0,1),(0,2),(0,3)]` | 拒：`port carries the values of nodes 2 20018 in the same cycle`（`PE_MEM_X0_Y1-T3.EAST_XBARI`，cycle 7） | — | — |
| reinject | 把 A* 自己的 23 条路径转成坐标回灌 | routes 23/23 接受，但报 3 处端口冲突（`6/9` @ `PE_X1_Y2-T2.WEST_O` cycle 6 等） | — | — |

exp3 是关键的一条：注入的坐标确实改变了端口分配，二进制变了，仿真仍然 243 项全对——说明"外部给定布线"这条路是通的，不是把 pin 塞进去自欺。

## 4. 发现

1. **一拍内可以穿多个 PE**。`9→10` 两跳、相位余量 0，`11→12` 三跳、余量 0，都能映射且仿真一致。所以相位差不是跳数下界，可行性只能由端口图搜索判定；放置阶段的代价函数也不能用"相位差 < 曼哈顿距离"这种硬约束去筛（§PLAN 4(a) 里那条硬约束要改）。
2. **fixture 必须同源**。库里的 `array_add_mem_alloc.txt`（bank `A,0 C,80`）与 `work/smoke` 由内存依赖生成的 `array_add_mem.json`（`A,128 C,0`）不是一套；混用会得到 39 个 mismatch，换回 smoke 那份 `Matches::243, Mismatches::0`。`scripts/armb_spike.py` 因此优先取 `work/smoke` 的 alloc。
3. **坐标级接口必要但不充分**。把内部 A* 的 23 条路径按坐标回灌，全部被接受，但贪心重定端口后仍在两处交叉开关端口上撞车。也就是说"每条边都合法"不等于"整组边可同时实现"，LLM 的输出必须能拿到端口/拥塞级反馈、或者校验器要带 rip-up 重排，否则重问循环会被这类不可见的冲突拖死。这是臂 B 最需要在 `LLMRouter` 里处理的问题。
4. 寄存器等待上限是打印器的真实限制，不是保守选择：超过 2 个寄存器端口的路径会让 `printHyCUBEBinary` 在 `southo` 取值处断言失败。

## 5. 复现

```bash
toolchain/build.sh            # 或 local 已构建
scripts/smoke.sh              # 产出 work/smoke/array_add_mem.json
python3 scripts/armb_spike.py bootstrap   # 拷 fixture + 记录 PathFinder 基线放置（各用例的 pins 来源）
python3 scripts/armb_spike.py matrix      # 上表 exp1..neg4
python3 scripts/armb_spike.py reinject    # 回灌 A* 自己的路线
```

改动都在 `toolchain/patches/0002-mapper.patch`（`src/mapper/LLMMapper.cpp` 新增 verify 模式，约 1280 行），从上游 pristine 打上该 patch 可还原 vendor 树。

## 6. 下一步

1. `SAPlacer`：产 `pins.json`，代价 `J = 总跳数 + 10·违反数 + 0.5·关键路径下界超出量`；硬约束去掉"相位差 ≥ 曼哈顿距离"，改成"相位差 ≥ 0 且不早于父节点"。
2. `LLMRouter`：输入每条边的起止 `(x,y,t)`、slack、当前端口占用热图；按冲突簇批量提问；失败时把 `report.conflicts[0]` 原文回给模型重问（≤3 次）。
3. 校验器补 rip-up：坐标接受后按拥塞代价重排端口，把 §3 `reinject` 那类冲突消掉，减少无谓重问。
4. `route_encode`：把已接受的坐标序列翻成 HyCUBE route code，供不经 mapper 直接写 bin 的对比实验用（可选，非 spike 必需）。
