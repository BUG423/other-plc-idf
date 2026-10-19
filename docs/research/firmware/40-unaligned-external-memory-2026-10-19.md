# Easy521：撤销外部变量的未验证对齐假设（2026-10-19）

## 1. 问题与证据边界

本轮修正 Linux 原生探针对 PLC 外部变量作出的 `align4` 假设。先前新 MatIEC 应用已经获得传输 ACK，协调者逐文件精确回读17项；随后 STOP读回为0，RUN请求ACK后首次GCM仍为0。用户进一步报告 RUN灯灭、ERR红灯亮。失败版本保留于 `.plcidf/native-integrated-matiec-20261019T075356Z/`，本轮没有覆盖它，没有操作设备。

发现的具体风险是：**既有变量地址不提供4字节对齐保证，但旧 adapter生成了直接访问外部地址的 VFP 内存指令。** 这与故障现象相符；物理错误码、实际页基址和故障指令仍未知，不能把它写成已经确认的唯一故障原因。

机器证据、两个新候选、实际命令与哈希见[40 JSON](40-unaligned-external-memory-2026-10-19.json)。[研究31](31-matiec-native-arm-2026-10-19.md)、[33](33-matiec-bound-application-2026-10-19.md)及失败设备记录均按原貌保留。

## 2. 对齐风险的局部来源

所检查的 Simulator `CompileSystem.dll` 在 `_elf_simplify_symbols`（VA `0x6a74a9d8` 起）对 type8 的 UND符号调用独立变量解析器。`_st_loader_elf_var_sym_addr`（`0x6a74c938`）进入 `_dev_mem_table_addressing`（`0x6a7490a0`）：

```text
page       = (operand >> 19) & 0xff
bit_offset = operand & 0x7ffff
pointer    = page.base + (bit_offset >> 3)
```

Pcmd的既有 `0x021000f0` 在该参考映射中为页66、字节偏移30。它不能被解释为一个自然4字节对齐的主机指针。实际 Easy521页基址与固件实现尚未读到；本轮只撤销未经验证的对齐承诺，没有把 Simulator直接等同于固件。

失败候选的 MAIN在 `+0xc`、条件路径 `+0x22` 直接 `VLDR [r0]`，在 `+0x2a` 直接 `VSTR [r0]`。VFP内存访问不能由普通 ARM非对齐 LDR/STR 的可用性推导安全。厂商 LAB对象对外部 REAL用整数 LDR/STR搬运位值，在其对齐临时区才使用VFP访存，为此风险提供了另一条局部交叉证据。

同时排除了“换旧库”这一猜测：实际正常 LED备份 `stprog.o`（SHA `69f3e60d…`）中，43个STCLib导出函数逐字匹配 `SysLib_New`，旧分支仅3个匹配；9个SysLib函数也与本轮库逐字匹配。当前17文件候选的 `symbol.sym`、`varsinfo.foid`、`userprog.foid` 与原机备份逐字相同，symbol的WORD仍为5。没有证据将该WORD盲改为LAB的3，或将合法type8改成普通绝对符号。

## 3. 最小代码修正

MatIEC的生成 `POUS.c/h` 不作任何修改。仅在自有 adapter中对外部 REAL声明和实际访问cast使用 `__attribute__((aligned(1)))` typedef，再用 `-mno-unaligned-access` 编译，避免仍依赖未知的CPU非对齐访问配置。不能仅加此开关而保留普通float的 `align4` 承诺。

实际生产链现在明确为：

```text
原ST → MatIEC生成C → Clang LLVM IR → Clang ARM汇编 → Clang assembler对象
```

IR中的唯一外部声明和每次volatile浮点load/store都必须为 `align1`；汇编必须通过实际字节访存审计，才允许交给assembler。报告分别记录真实 `MAIN.ll`、`MAIN.s` 的路径、哈希与消费关系，它们不是与最终对象无关的展示文件。仍检查只定义 MAIN、只引用既有 Pcmd、固定重定位及ARM属性。

旧 `restricted-llvm` 的 REAL以及尚无合法地址分配保证的DINT探针同样改为 `align1`，LLC固定启用 `+strict-align`。对象编译后另保存相同冻结IR、相同后端参数的实际汇编进行审计；不把此配对审计叫作运行时执行。真实测试另外使用LLVM objdump检查对象指令。

审计拒绝通过一般寄存器地址的非字节LDR/STR、VFP访存或helper调用，允许已有AAPCS栈访问和PC相对常量读取；未知输出形式失败退出。没有新增设备指令、I/O、变量分配、强制值或中断调度钩子。

**字节访问不是原子操作。** 本profile只支持单个应用writer，没有实现并发writer同步；USB并发读取可能看到尚在拼接中的REAL。RUN时单个离群值不能被静默过滤或直接当作ST规则错误，应保留原始位值和时间，并以STOP后的稳定快照作更强的观察。报告保持 `access_atomic=false`、`concurrent_writer_supported=false`、`concurrent_reader_coherence_verified=false`。

## 4. 新的不可变候选

源仍为公开的 `examples/easy521_native_memory/conditional_plus{1,2}.st`，阈值10、增量1/2。两份正文、生成C均与失败版本逐字相同。变量分配、两库、任务/配置和symbol来源都未改变。

| 产物 | +1 SHA-256 | +2 SHA-256 |
| --- | --- | --- |
| 新MAIN.o | `7f34108912a4d9763324a80e8819504e023c29fe66f4025701d4700757050e28` | `f61cdbec7dc821fabea975f025bbc3a4ce583f8f8073d64afcd23d578fcb624d` |
| bound/stuserprog.o，4460 B | `756c8877c3c80c4f31a68e7fd29add38a632f87aedd5496f0bdde6f8ca3fc6b9` | `4fe3929a43a082f2176fa760f7ffcd82f44cc89796d5ffcd3d763fe01c11cdc5` |
| bound/stprog.o，143980 B | `4ece269ba5a3f7d9a18785edd2a89a8c9450803cfa0804bd4373a40619bd9304` | `28009b49f5edd2dbed710ef0851c4183d1e324bc72827a9d234db53a55999b17` |

两份MAIN均112字节，真实对象独立反汇编见8个LDRB、4个STRB；只剩一次读取PC相对对齐常量的VFP访存，没有通过外部地址执行VFP或word访存。

固定构建入口仍为 `bind_native_application.py --compiler-backend matiec-clang`。新候选报告为：

- `.plcidf/research-40-unaligned-20261019/plus1-build/candidate-report.json`
- `.plcidf/research-40-unaligned-20261019/plus2-build/candidate-report.json`

目录内 `native-main/matiec-probe-report.json` 记录IR/汇编/对象，`linked/link-report.json`记录两级链接，`bound/`为新的两个绑定对象。它们仍须由协调者组装并独立验收，没有将下载或运行状态改为true。

## 5. 验证与后续

相关三份测试 **59项、36个subtests通过**，使用本机实际MatIEC、Clang、LLC和LLD。新增验证包括：

- REAL与未知地址DINT都不作align4承诺，错误IR/一般寄存器word或VFP访存/helper被拒绝。
- 真实C→受审计IR→受审计汇编→对象的输入链，以及restricted两profile真实对象的独立反汇编。
- 将同一MatIEC生成C和adapter在Linux主机链接到**偏移2字节**的存储，启用UBSan，验证单writer语义、入口不重置变量及相邻字节不被修改。
- 原源码拒绝、超时不重试、编译后来源漂移、固定接口/库/任务约束和两级绑定继续通过。

主机行为、参考loader及真实ARM对象证据分别报告；它们均不替代Easy521的故障码、恢复、下装和RUN/STOP实测。协调者后续实验应关联这两份新候选，保持失败记录原貌。若新实验仍失败，需要原机精确基线及实际诊断作对照，不能以反复RUN、盲改库或metadata代替定位。
