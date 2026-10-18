# 不依赖 24V 的主机工具链推进（2026-10-18）

用户要求继续自主推进。本批三个 AI 技术代理分别实施厂商编译前处理、部署追溯和能力诊断/分发，统筹代理负责独立正负联测、AutoShop 工作副本操作、集成和 GitHub 同步。本轮没有确认下载、RUN、STOP、设备通信 DLL 或固件操作。

## 普通 ST 正文已能脱离 AutoShop 界面编译为 ARM 对象

新增 `tools/prepare_vendor_st.py`：UTF-8 MAIN 正文和显式 BOOL/INT/DINT/REAL 全局表生成新的 MAIN.st/MAIN.cl，再交给既有独立 StLang worker。新输入是我们自行生成的，不再依赖旧工程冻结的预处理文件。

统筹独立创建的真实实验：

| 输入 | 最终结果 |
|---|---|
| `COUNTER := COUNTER + 1;` | 新 ARM ELF32/REL，2040B，SHA256 `9d2063daa05318c463c27a43ae6895e79fe27ba6d34dadd08dc27eb0e22affd1` |
| `COUNTER := COUNTER + 2;` | 新 ARM 对象，2040B，SHA256 `ef27685da39c95eedb3c942d64b2a3ee78ade4d50ce7897d73041213bf26add4`；`.text` 机器代码段哈希也不同 |
| +1 输入在另一新目录重编译 | 对象字节一致，排除复用旧产物 |
| `COUNTER := ;` | 中间准备完成，真实厂商编译器拒绝；没有把 prepared 当编译成功 |
| 四基础类型、多语句及 IF | 最初触发厂商编译器 `0xc0000005`；修复后实际生成 2600B ARM 对象，SHA256 `3bf9cee57ea60ad8c994a226c775f89bac4de7b60ae0c937637ecc8daad9d570` |

异常来自缺少厂商前处理隐式生成的 `_HC_EMPTYFUC:VOID()` 声明。发布工具补齐固定声明后，统筹重新用新目录复核全部正负输入。原异常记录保留，未被最终成功覆盖。实施代理另外实测单变量多语句、双 DINT、256 个 DINT 声明。工具的注释、字符串和不支持语法检查均有离线回归。

这是 **受限 ST 文本 → 厂商 ARM 可重定位对象**。尚未完成库/软元件/变量地址与保持区绑定、任务中断、完整程序链接/封装、独立设备协议或 PLC 执行。隐式函数只是声明，完整链接还需要厂商运行时。仍依赖本机 Windows StLang 编译器，但不占用 AutoShop 界面。

使用方法与原厂接口证据见 [研究报告 11](../research/firmware/11-plain-main-object-2026-10-18.md)。统筹原始结果位于 [`evidence/easy521-application-20261018/`](../../evidence/easy521-application-20261018/) 的 `final-plain-*-object.json`；机器代码段比较和首次异常均单独保留。

## 自动重编译追溯：补齐本地候选链，保留设备字节缺口

AutoShop 初始没有运行。本批只启动已保存的独立工作副本，确认 MAIN 仍是 revision 2 内存探针，并执行真实 F7 编译。没有编辑原始 BESS 工程。

首次完整覆盖检查发现旧构建清单漏掉 `PLC/PLCInsIndex.idx`、`PLCProg.prg`、`stuserprog.o`、`symbol.sym`、`userprog.foid`。已将 native build 的源选择改为完整目录扫描，保留明确的生成目录/锁定归档排除；未知子目录不再悄悄遗漏。重新 F7 后形成覆盖 80 项输入的 verified 清单。

打开 F8 设置但不确认，选定顶层、STTemp、Temp、PLC 共 108 个文件前后哈希一致；此时没有新 F8 编译，旧 F7 日志不能算新编译。此前确认下载后出现的重编译/文件删除问题仍存在。两次 F8 设置均以精确“取消”按钮关闭。

新增 `native_release.py`：保留选定 build/intent；确认前候选复制到独占目录，release.json 和候选只写一次，绑定源/变量/配置、原构建和转换。真实工作副本 `freeze_intent → compile_baseline → capture_release → verify_release` 通过，转换为 `unchanged-pre-confirm`，`compile=null`。确认后变化仍返回 unknown，不采用新哈希、不重放。工程一致性和设备字节一致分别报告，缺少实际字节绑定时顶层仍未核验。见 [ADR-0004](../architecture/ADR-0004-vendor-build-release.md)。

实际字节副本留在本机；公开 `local-release-record.json`、输入/候选哈希和会话摘要。没有用本地快照证明 PLC 接收了相同字节，也没有重新下装验证新版编排。

## 诊断、分发与错误反馈

`doctor --json` 现在分别解释完整源码构建、对象编译、下载候选/工程一致性、设备字节身份、运行和只读 USB 表格。提供缺失条件与可执行 argv，仍是纯本地文件检查，不启动 Windows、不枚举 USB、不读取历史成功记录来冒充当前状态。磁盘 FileVersion 与当前窗口版本分列；本机 EXE 写 4.12.0.0、窗口显示 4.12.0.2，不能混为同一版本来源。

两个 Python worker 与 PS helper 已纳入独立 wheel，并验证仓库外的正文准备流程。[诊断用法](../usage/easy521-doctor.md) 记录入口与能力边界。

同时修复两个实测边界：Windows 在 UTF-8 初始化之前输出 ANSI 错误时，interop 保留退出码和诊断，不再抛 Unicode traceback；错误数解析不跨行吞掉下一行年份。真实本机子进程负例确认非 UTF-8 错误之后有副作用的调用不会被自动重放。

## 验证与后续

源码/监测回归 765 passed、51 skipped、1 deselected；工具测试 114 passed，另 499 个 subtest 通过。两组测试路径不重叠，共 879 passed；分项专项结果包含在其中，不再相加。独立安装结果及最终源码哈希另存入 `docs/management/evidence/`。

下一编译缺口是受限对象与完整应用的链接/包装、软元件和运行库绑定；下一部署缺口是确认后实际传输对象/设备字节读回。供电及 CPU 执行仍需现场条件，本轮没有重新观察设备供电或运行状态，不把昨天的现场事实当成今天的新观测。
