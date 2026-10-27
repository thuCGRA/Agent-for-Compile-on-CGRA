# 拥塞膝点工具（arm B）

- `gen_all.sh`：把 `toolchain/vendor/dfggen/benchmarks/morpher_benchmarks/*/` 的 C kernel
  经 dfggen pass 生成 `*_PartPredDFG.xml`，落在 `work/armB/dfggen/<kernel>/`。
  只跑 `clang → opt → opt -load libdfggenPass.so`，**不链接、不跑二进制**，XML 是 pass 直接写出的。
- `make_arch.py <X> <Y> <out.json> [src.json]`：从可用的 4x4 arch json 派生 NxM 网格。
  必须删掉显式的 `CGRA.CONNECTIONS`（GRID 模式自己推导），否则 `ParseCGRA` 触发 `dest_mod` 断言。
- `sweep2.py`：kernel × 尺寸扫描（k=0，II 自由）。
- `sweep3.py`：密度阶梯（同 kernel 复制 k 份，结构保持：idx 偏移、BB 名加 `_cN`、每份独立 MutexBB）。

口径与结论见 `docs/ARM_B_KNEE.md`。
