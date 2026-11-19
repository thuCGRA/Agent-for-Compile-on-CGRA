/*
 * 占位文件，不是真的 glibc 头。
 *
 * 为什么会有这个东西：DFG 生成用 `clang -target i386-unknown-linux-gnu`，
 * 目的是让指针变成 32 位、DFG 里 base pointer 的位宽和 HyCUBE 的地址宽度对齐。
 * 但一旦指定 i386 target，clang 就会去 sysroot 里找 gnu/stubs-32.h，
 * 而 conda-forge 的 clang sysroot 只带 64 位 glibc 头，于是报
 *   fatal error: 'gnu/stubs-32.h' file not found
 *
 * 我们只需要把 C 编译成 LLVM IR，从不链接 32 位 libc（真正跑测试向量是用
 * native/m32 另一条路径，见 scripts/gen_trace.sh），所以这里放一个空壳，
 * 让 include 能过即可。gen_dfg.sh 用 -isystem 把本目录排在 sysroot 前面。
 */
#define __stub_dummy 0
