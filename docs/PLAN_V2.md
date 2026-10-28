# 实验计划 v2：从"让模型守规矩"转到"把不可判的部分交给编译器"

## 现状（要解决的问题）

- LLM 直接给 (x,y,t)：10 kernel 里 3–4 个 strict 可映射，其余全灭；4 次独立抽样 + 4 次反馈重试后，6 个 kernel 仍然 0/4。
- 已证明：合法解区域很窄（gemm 随机空间 6/6 不可定时），反馈只能修"差一点"的解。
- 已证明：把相位交给编译器（`tools/legalize.py`）能救回 array_add 与 gemm（134c 387/0、37c 783/0）。

## 假设

H1（空间对、时间错）：模型选 (x,y) 的能力强于选 t 的能力。把 t 交给编译器后，通过率显著上升。
H2（空间也错，但只是局部错）：剩余失败是少数节点落进了不可定时的角落；以 LLM 的空间为起点做局部搜索，能救回大部分。
H3（模型的价值不在单点质量）：在同等预算下，LLM 空间 + 编译器修复 的最终 lat/cycles 不劣于纯 A*，且调用次数少于多次抽样。

## 实验

E1 空间/时间分离（验证 H1）
  10 kernel，II 按 kernel（gemm_pace=9，其余 4）。取 LLM 的 (x,y)，`legalize.schedule` 定时，strict 映射，有 trace 的跑仿真。
  指标：map 成功数、lat、cycles、mismatch。

E2 空间局部修复（验证 H2）
  对 E1 失败的 kernel，以 LLM 空间为初始解，做带预算的局部搜索：每次改一个节点的 (x,y)，用 legalize 可解性 + mapper 可映射性打分，爬山/退火，预算 N 次 legalize。
  对照：同预算的随机重启（证明不是预算的功劳）。

E3 三方对照（验证 H3）
  LLM+legalize（E1/E2）vs `-m 0` 基线 vs SA 放置器，比 lat、mapper 时间、LLM 调用次数。

## 顺带要修的装置问题

F1 评测里 `pins_applied` 仍会累加（conv3 报 56/28、gemm 46/26）：评测侧按最终 placement 去重计数。
F2 第二轮调用空响应：已降级记录；E1 不再依赖第二轮。
F3 E1/E2 的每个 job 落盘（placement、legalize 日志、mapper 报告），便于复查。

## 判据

E1 若 map 成功率 ≥ 7/10，H1 成立，主线改为"LLM 定空间、编译器定时间"。
E2 若在 E1 基础上再救回 ≥2 个 kernel，H2 成立，写成"propose-repair"方法。
两者都不成立，则按上一轮结论收束为能力边界论文。