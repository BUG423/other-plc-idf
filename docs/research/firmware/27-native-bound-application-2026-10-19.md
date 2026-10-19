# 固定既有工程接口：Linux ST 编译、两级链接与合法全局绑定

2026-10-19。本轮把[研究 23](23-linux-st-abi-2026-10-19.md)的原生编译探针接到真实既有工程分配，首次在一个 Linux 调用中完成：**新 ST 正文 → 新 MAIN ARM 对象 → 两级 LLD 链接 → 同工程 GVT/CSV 推导的 SInfo 绑定**。`+1.0` 与 `+2.0` 两条实际新链都成功，输出两个不同的 `stprog.o`。本轮不是给旧对象补一个自报源码哈希；MAIN 确由本轮 Linux 前端和 LLVM 实际编译，再按对象身份进入链接、绑定。

新增 [bind_native_application.py](../../../tools/bind_native_application.py)，扩展 [compile_easy521_native_probe.py](../../../tools/compile_easy521_native_probe.py) 的显式 REAL profile。所有原始文件、对象和完整反汇编留在私有目录；公开的输入/工具/源码/产物哈希、绑定字段与测试记录见[元数据](27-native-bound-application-2026-10-19.json)。**仍没有完整应用配置封装、ARM 执行、下装或真机读回，本轮所有 `complete_application/runtime_abi_verified/downloadable` 均为 false。**没有执行 Windows、GUI、网络或设备操作。

## 1. 为什么使用既有 REAL，而不是把 COUNTER 硬绑到一个地址

只读枚举现有工程、TEST/BENCH 与研究副本中的真实 GVT 后，只有同一个已知合法分配：19 项变量，包括 14 REAL、4 BOOL、1 INT；没有 DINT COUNTER。原 DINT 编译 profile 仍保持，但不能凭空给它分配地址。

本轮新增明确命名的 `bess-pcmd-real-increment-v1`，只接受这一声明：

```json
{"schema":"plcidf.vendor-main-source.v1","globals":{"g_rBESS_Pcmd":"REAL"}}
```

以及下列一条完整正文，增量只允许 `1.0` 或 `2.0`：

```iecst
(* 固定工程中的纯内存诊断探针；每次 MAIN 调用增加一个诊断步长。
   此处临时复用既有 REAL 存储，不执行原 BESS 功率控制业务。
   任务周期、初值及保持配置沿用下述已冻结合法接口；没有物理 I/O。
   停止扫描后本函数不再改变变量，未加入单独的业务停止/复位状态机。 *)
g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0;
```

其他变量、运算符、第二条语句、IF、定时器、函数、直接 I/O 和其他常量形式均拒绝。变量的业务名称不代表本候选仍运行原 BESS 控制逻辑；此候选**替换 MAIN 正文**，只复用声明和运行配置。

## 2. 同工程分配与任务接口的约束

固定候选 profile 为 `bess-easy521-existing-pcmd-v1`。实际输入根为 `/mnt/e/PLCProjects/PLCIDF_LAB_20261017/BESS_E521_DEV`，工具只接受下面 12 项已观察文件的完整 SHA-256，不接受“名字一样但配置不同”的工程：

- `变量表.gvt`、`VarNameAndAddr.csv`、`VarsInfo.vi`、`ReAllocVarsInfo.vi`。
- `ILIndex.idx`、`Output.prg`、`Output.sdt`、`Config.sdt`。
- `Temp/userprog.foid`、`Temp/varsinfo.foid`、`Temp/symbol.sym`、`STTemp/SBR_001.o`。

配置代理独立发现，userprog 的相关段与 `Output.prg` 和 `ILIndex.idx[30:]` 一致，变量段与 `VarsInfo.vi` 一致，符号表保留 MAIN/SBR_001 标识。这里既核对原样哈希，又显式解析 ILIndex：magic `10000001`、版本 1、26 字节头、28 项 LE DWORD；第 16/19/24 项为 0/1/2，其余为 `0xffffffff`。**这些索引本身不被解释成毫秒周期或 CPU 地址。**原 SBR_001 为固定的既有预编译输入，MAIN/SBR_001 名称保持。

地址只来自 GVT 解析器，独立 CSV 对所有 19 项的地址/位宽逐项核对；没有从参考 ELF 或另一个项目复制地址：

| 所选项 | 本轮验证 |
|---|---|
| GVT 名称 / ELF 名称 | `g_rBESS_Pcmd` / 固定已验证映射 `G_RBESS_PCMD` |
| 声明 / 位宽 | REAL / 32 bits |
| GVT 类型码 | 32；与 SInfo 类别 8 明确区分 |
| 既有符号地址 | `0x021000f0`，来自 GVT 文件偏移 `0x394` |
| 独立 CSV | 同名地址与 32 bits 一致 |
| 序列化初值 | 文本 `0`；不据此声称设备当前值为 0 |
| GVT flag | 固定原样 `01 01 00 00`，未臆测每个位的含义 |
| 保持属性 / 周期数值 | `unknown`；冻结并复核 GVT、Config、VarsInfo、Output、ILIndex 原字节 |

保持与周期的验收是“既有合法序列化配置未改变”，并非其全部语义已解码。工具不创建变量、不改初始化或保持区、不改任务表；GVT 地址继续保持厂商不透明符号地址，不能除 8 当成裸 RAM 或 Modbus/D 寄存器地址。与当前在线设备属于同一应用的证明，仍需独立设备身份与下载/校验记录。

## 3. REAL 编译的局部 ABI 与算术证据

读取研究 11 保留的自写厂商样本 `VALUE:REAL; VALUE := VALUE + 0.5;`，其对象 SHA-256 为 `3a3b16d5b68f4588e7687d3970ce965f7dfdad1e77921da20c078eed774c2da7`。局部序列为 32 位浮点读入、扩展为 double、按 r0/r1/r2/r3 调 `_ST_DOUBLE_ADD`、缩回 float 并写入。冻结 `_SysLib.o` 中该 helper 只做寄存器搬运和 `vadd.f64`，没有额外内存写或后续调用。

新前端因此生成 `float load → fpext double → fadd double → fptrunc float → float store`，没有 fast-math 标志、没有复制厂商代码或读取其对象来生成 IR。LLVM 产物只引用 `G_RBESS_PCMD`，通过 Thumb MOVW/MOVT 外部重定位取得地址，直接执行浮点转换/加法，不再外调 helper。ARM EABI5、Thumb-2、VFPv3/NEON 和保留 R9 的属性延续研究 23 的约束。局部运算和访存对应不等于完整调用方、任务、浮点状态或机内 ABI 已验证。

同一 IR 另外编成 x86 对象真实执行，两个步长分别从 `0.0,-1.5,2^24,2^25,2^-149` 开始扫描三次，合计 **30 个新样本**，逐个 float32 原始位值吻合。特别是 REAL 在 `2^24` 附近已经不能表示所有相邻整数：`+1.0` 可能保持原值，`+2.0` 可以继续改变。这是正确的浮点精度行为，不能把它当作整数计数器溢出规则。主机执行不被当成 ARM 或 PLC 执行证据。

## 4. 新工具真实执行、关联与有限修改

聚合工具在全新目录冻结 12 项接口和两库，直接调用真实原生编译，再按 `新 MAIN.o → 原 SBR_001.o → _SysLib.o` 和 `stuserprog.o → _STCLib.o` 两阶段链接。两库严格限定为研究 16 的 SysLib_New 分支；只有 MAIN 从新 ST 编译，SBR 和库明确记录为复用的预编译输入。

编译器消耗的源/声明哈希必须与聚合批次相同，链接器第一项输入必须与新编译 POU 相同。每个新链接对象中，仅允许所选 Pcmd 的 `st_value` 和 `st_info` 改变：

```text
之前：st_value=0, st_info=0x10, st_shndx=UND
之后：st_value=GVT解析地址, st_info=0x18, st_shndx=UND
```

这复用研究 16/17 证实的 SInfo 类别 8 与字段更新规则，不把全局符号改成普通绝对 ELF 符号。两个对象各只有 **4 个字节实际不同**，全部落在允许的 5 个字段字节范围内；代码、重定位及其他字节不变。剩余运行库外部符号仍存在，不能据此称所有运行时依赖都已解决。

最后重新读取所有原输入、冻结副本、工具组件、源对象、链接产物和绑定输出。报告 schema 为 `plcidf.native-bound-application.v1`；成功时 `source_compilation_verified/existing_allocation_verified/source_to_bound_objects_verified=true`，具体含义限于本轮 MAIN 和固定本地输入链，没有历史构建认证或在线设备认证。

## 5. 稳定候选与复现

最终目录均为新的私有目录，旧 `first-plus*`、`candidate-plus*` 中间结果原样保留：

| 步长 | 最终目录 | MAIN SHA-256 | bound/stprog.o SHA-256 |
|---|---|---|---|
| +1.0 | `.plcidf/research-27-native-bound-20261019/candidate-final-plus1/` | `e72500955d9b0962d3a085a414e0d90539a445c6879e060fc16f12ed9470a385` | `318040ea24813ea691a6d7f17129a30fbbb1aa83bef3a30340d3ade2e370fb90` |
| +2.0 | `.plcidf/research-27-native-bound-20261019/candidate-final-plus2/` | `b32360fd713d230e9673ef1e46b9cfbb6cb44b5d89f2e6fce88203a52a76c6ee` | `314ecb7828b691975b90814aa8ab7d04ce16c3236d0f0f5b0b83a09841c15332` |

两种 MAIN 均为 760 B，函数为 34 B；两个阶段的绑定输出分别为 4,188 B、143,708 B。每个目录包含 `candidate-report.json`、`native-main/probe-report.json`、`linked/link-report.json`、`bound/stuserprog.o`、`bound/stprog.o`，以及冻结接口与库。完整配置候选应引用这些报告和哈希，不直接复制旧工程的组合对象。

使用同一已知源文件重复真实编译，输出目录必须换成全新名称：

```bash
.venv/bin/python tools/bind_native_application.py \
  --source .plcidf/research-27-native-bound-20261019/sources/plus1.st \
  --globals-path .plcidf/research-27-native-bound-20261019/sources/globals.json \
  --project-dir /mnt/e/PLCProjects/PLCIDF_LAB_20261017/BESS_E521_DEV \
  --syslib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_SysLib.o \
  --stclib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_STCLib.o \
  --llc .plcidf/research-tools/llvm20/extracted/usr/lib/llvm-20/bin/llc \
  --lld .plcidf/research-tools/lld20/extracted/usr/lib/llvm-20/bin/ld.lld \
  --output-dir .plcidf/research-27-native-fresh-candidate
```

只编译新 REAL profile 时，用原生探针工具加 `--profile bess-pcmd-real-increment-v1`；其默认仍是研究 23 的旧 DINT profile。

## 6. 验证与下一验收

本轮相关测试共 **31 项通过、0 跳过**：编译工具 20 项（5 项真实 LLVM 方法），新增聚合工具 11 项（2 项真实 LLVM+LLD 方法）。聚合测试使用独立生成的 GVT/ELF/配置占位输入，显式在测试作用域替换 profile 哈希，不嵌入厂商 fixture；上面的实际工程实验另行验证生产 profile。测试没有通过修改生产期望或忽略未知字段来扩大成功范围。

```bash
PLCIDF_TEST_LLC="$PWD/.plcidf/research-tools/llvm20/extracted/usr/lib/llvm-20/bin/llc" \
PLCIDF_TEST_LLD="$PWD/.plcidf/research-tools/lld20/extracted/usr/lib/llvm-20/bin/ld.lld" \
  .venv/bin/python -m unittest discover -s tools/tests \
  -p test_bind_native_application.py -v
```

编译工具测试另以 `-p test_compile_easy521_native_probe.py` 执行。负例涵盖额外 ST 语句、错误标量类型/初值、CSV 冲突、任务表变化、保持配置字节变化、错误库、现存目录、超时不重放、真实编译过程中原工程变化，以及源码/IR/架构漂移拒绝。未设置本机 LLVM/LLD 环境变量时，真实后端方法明确跳过。

配置代理接下来把这两个新对象、固定变量/任务接口及完整配置/文件闭包组合为可审查候选；纯 Linux 会话与原生下载由另一条任务实现。实际验收仍须在确认目标身份后完成应用下装、设备字节核对、RUN、Pcmd 原始位值/时间序列读取、STOP、改步长重新部署与读回。只有该链完成，才可以把本地候选进展提升为用户所需的 Easy521 纯 Linux 运行闭环。
