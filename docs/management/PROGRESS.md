# 进展记录

## 2026-10-17：通用输入输出与 AI 一键验收交付

用户确认当前只有 Easy521，优先完善 WSL/Linux 中可由 AI 直接创建、修改、编译与验证的工作流。[交付报告](AI_WORKFLOW_DELIVERY_2026-10-17.md) 与 [操作说明](../AI_PROGRAMMING.md) 已补齐。

- 新 `open-linux-signals-v1` 支持最多 64 个 BOOL/INT/DINT/UINT/UDINT/REAL 直接 MAIN 字段。真正生成的类型与契约核对；旧 counter 工程和 release 保留兼容路径。
- `new --template motor` 带 ST、manifest、场景与 AGENTS.md；`verify PATH --json` 实际编译、部署、确定扫描执行并断言，失败关联 expected/actual 与场景/步骤/扫描。
- motor 6 场景/56 断言、analog 3 场景/16 断言通过；60→100ms 规则变化先被旧测试检出，新边界 t80ms 停/t100ms 起动真实通过；7/60/1000ms 周期均实测。
- 默认软件 535 passed、51 skipped、1 deselected；启用真实工具链的后端与验收套件 192 passed，与默认套件重叠。独立 wheel 的仓库外新建/实际编译/验收通过。中断后的进程清理和 CLI 结构化退出 130 已覆盖。
- 由三个 AI 技术代理并行实现，统筹代理做 CLI、独立正负联测、打包复核、CI 与 GitHub 同步。T13/#17 交付待合并，T12 仍只有本机语义基础；没有新 Windows/PLC 操作。

开放软件运行结果不替代 Easy521 设备运行。T09 的应用下载/运行、R01/R02 的厂商协议与系统固件研究继续单独跟踪。定期 GitHub 报告需合并默认分支后启用。

## 2026-10-17：三路线并行实施，开放 ST 首个闭环交付

用户确认三路线同时推进，以路线 2 优先。[本轮交付报告](PARALLEL_DELIVERY_2026-10-17.md) 记录代码入口、命令、实测证据及剩余工作。

- 路线 2：统一 CLI 已接入 `open-linux`，真实 ST 经 MatIEC/C/GCC 执行；计数、心跳、停止、完成、修改源码产生不同行为，以及改包拒绝均通过。默认工具链已在本机缓存验证，完整 Beremiz、远程板卡与物理 I/O 尚未接入。
- 路线 1：交付标准库应用产物/差分检查工具，实际 12 文件与 6 组对照，发现 Output.prg/sdt 不可单独绑定本次逻辑；完整 ABI/下载仍开放。
- 路线 3：交付有界 ELF/uImage/DTB/平台标识检查器与硬件证据表，35 项测试通过；实际设备镜像仍未取得，准确 SoC/OS/BSP 仍 unknown。
- 验证：默认源码套件 396 passed、13 skipped、1 deselected；实际工具链下后端套件 38 passed（与默认套件有重叠）；工具/汇报 54 项；最终 CLI 正负验收与独立分发通过。新增 CI 原生 ST job。没有新 Windows/USB/PLC 操作。

T11 已交付待合并；T10 部分交付；T12 与 R01/R02 剩余验收保持开放。新结果不替代 Easy521 实际下载、运行、I/O 或硬件周期验收。

## 2026-10-17：原生工具链与多 PLC 范围扩展

用户明确要求探索绕过 Windows AutoShop，并支持多种 PLC。已采纳 [ADR-0002](../architecture/ADR-0002-native-multitarget.md) 的双路线：保留厂商运行时的兼容研究与自控开放运行时并行；已有桥接保留为对照和兼容路径。

- **新增离线实验**：[Linux LLD 部分链接](../research/firmware/06-linux-link-experiment-2026-10-17.md) 成功处理本轮 ARM 对象；完整三库组合因重复符号失败，排除重复占位对象后可部分链接，仍有 46 个未解析名字。没有完成 LiteST 前端、完整 ABI、包装、下载或运行兼容。
- **新增本机证据**：[工具链台账](../research/firmware/05-local-toolchain-2026-10-17.md) 区分 Windows ST 前端、ARM 目标库及宿主 x86-64 Linux 库；本机附带说明提及 Linux 烧录库，但没有取得对应主库或验证 Easy521 适用性。未复制厂商原库、接口文档或定义到公开仓库。
- **新增官方来源**：[公开来源复核](../research/firmware/04-official-sources-2026-10-17.md) 找到官方固件归档元数据，未下载系统镜像；公开的另一 Easy521 型号页面写明四核，不足以确认本机 SoC、OS、BSP 或 I/O。
- **任务与接口**：新增 M4 与 [T10 / #14](https://github.com/BUG423/other-plc-idf/issues/14)、[T11 / #15](https://github.com/BUG423/other-plc-idf/issues/15)、[T12 / #16](https://github.com/BUG423/other-plc-idf/issues/16)。分别落实目标/设备/传输解耦、无 Windows 的真实 ST 闭环、跨目标语义对照；均为待执行任务。R01/R02 继续追踪厂商 ABI/协议及板级/系统固件研究。
- **实际执行边界**：本轮仅公开资料、主机文件分析和原生 Linux 链接实验，没有 AutoShop/Windows 进程、USB、PLC 或固件操作，没有运行候选开放 PLC runtime。既有真机下载/运行与网口验收仍未完成。

执行角色为固件来源研究、可移植架构及编译器实验 AI 代理，由统筹交叉复核。新增 JSON、来源哈希、链接输入输出/重定位与预期失败证据已复核；这次不修改应用软件，持续集成结果随提交更新。

## 2026-10-17：双线接入后的实施与现场复核

最新报告：[LIVE_VALIDATION_2026-10-17.md](LIVE_VALIDATION_2026-10-17.md)。以下首轮审计记录保留为时间快照，当前能力以此更新为准。

- **T03 已交付待合并**：资源/依赖封装、独立 wheel、sdist 到全新 venv 的仓库外运行、三场景验证与 CI 检查。
- **T04 已交付待合并**：`new/init/check`、版本化 manifest、内存计数/心跳示例、真实导入表及有限静态检查。不会伪造原生工程。
- **T05 部分交付**：`doctor` 与若干命令的结构化 JSON、稳定错误输出、校准参数与版本声明透传；尚未实现全部命令统一协议或后端注册。
- **T06 部分交付且已实测**：已有工程 MAIN diff/读回/保存、`build --source` 的新产物与日志验证。真实负向错误定位行 26、退出 1；恢复正确源码后 verified、退出 0。自动变量/任务同步、全版本覆盖仍开放。
- **T07 基础交付**：BUSID 与 VID/PID 同时匹配、跨项目进程租约、严格证据、构建/计划哈希及篡改检查。新编译流程已使用租约/证据/清单；完整设备部署入口与端侧校验仍未贯穿。
- **T08 软件质量交付，现场受阻**：字序不明退出 3；原始值、质量、量程、契约哈希与操作者声明版本进入 JSONL。USB 查询确认 Easy521/6.11.0.1；Realtek 网卡最终仍 Disconnected/0 bps，没有真机 Modbus 成功证据。
- **T09 下载/运行尚未执行，备份已取得**：供电/网口灯、当前输出隔离及开关状态的核对未返回；本轮没有程序下载或 RUN/STOP、固件操作。首次取消上载入口后，已另建 Easy521/LiteST 工程完成 USB 上载，厂商日志明确成功，62 文件与本地 ZIP 哈希已留存，MAIN 读回与仓库一致；恢复演练未做。

原 Windows 工程 120 文件的收尾哈希均与快照一致；所有编译实验在另一个工作副本进行。当前交互窗口保留工作副本 MAIN，原源码已恢复并保存；现场适配失误与纠正均如实记入报告。

本地软件回归 **346 passed、1 deselected**（排除真实 USB 枚举用例），汇报脚本另 **5 项通过**，Windows 生产标题校验函数另 **10 项通过**；sdist→wheel→新 venv→仓库外三场景验证通过。分发证据与 GitHub CI 随本轮提交更新，见 PR 的检查结果及 `docs/management/evidence/`；不把源文件检查、虚拟场景或编译成功当作真机运行验收。

## 2026-10-17：基线审计与首轮修复

当前工作分支：`codex/plc-idf-baseline-20261017`。审计起点：`d87e5da`。

### 已确认

- 仓库是 PLC-IDF 原型，目标 Easy521。存在五个 CLI 子命令、Windows GUI 调用链、固定 BESS 寄存器监测、虚拟场景播放器及算法/AutoShop 历史工程资产。
- `init/check/sync/doctor`、通用用户 ST 仿真、机读构建诊断、产物清单、设备租约与下载后校验尚未完成。历史人工下载证据不能证明当前 CLI 自动闭环已验收。
- 原有 build/flash 根据 PowerShell 退出码判成功，必需校验未实现。已交由实现代理修复“未知结果误报成功”、配置类型与 GUI 重试；最终结果见下方验证记录。
- 现有厂商目标文件提供 ARMv7 应用目标和厂商 ABI 线索。芯片具体型号、板上系统、启动链及 I/O 驱动仍不能据此确定。详见 [离线与公开资料评估](../research/firmware/03-feasibility-assessment-2026-10-17.md)。

### 技术分工与交付

| 技术执行角色 | 本轮工作 | 交付 |
|---|---|---|
| 架构审计 AI 代理 | 第一性原理、设计与实现差距、规范勘误 | [架构评审](../reviews/ARCHITECTURE_REVIEW_2026-10-17.md)、SPEC/ARCH 注记 |
| 实现与测试 AI 代理 | 软件回归、缺陷复现、有界 P0 修复 | [实现审计](../reviews/IMPLEMENTATION_AUDIT_2026-10-17.md)、src 修复和测试 |
| 固件研究 AI 代理 | 官方资料与本地 ELF 分析 | [研究评估](../research/firmware/03-feasibility-assessment-2026-10-17.md)、离线样本哈希与输出 |
| 统筹代理 | 交叉复核、需求/任务、CI、GitHub 同步 | [实施计划](PLAN.md)、任务 Issue、进度机制 |

### 验证记录

修复前：CLI 80 passed，监测工具 65 passed。CLI 基线套件中一个测试执行了 `usbipd list` 只读枚举；未进行 attach/detach、GUI 驱动、串口通信、PLC 下载或运行控制。后续软件验证明确排除该测试。

修复后独立集成复核：`.venv/bin/python -m pytest src/plc_idf/tests wsl-tools/tests -q -k 'not test_devices_real_readonly'` 退出 0；对应 CLI 95 项 + 监测工具 65 项，共 160 项通过、1 项排除。进展脚本另有 5 项离线测试，覆盖人工正文不被覆写、事件防循环和汇总渲染。`git diff --check`、新增 JSON、本地文档链接与四份 ELF 样本 SHA-256 检查通过。

三项 P0 修复已落地：授权配置仅接受布尔 true；改变状态的 GUI 操作不自动重试；必需校验未实现时返回 unverified、ok=false 和退出 1。退出 1 不能推出设备未改变，亦不能据此自动重跑。真实编译/下载验证、目标身份与互斥仍由 T06/T07/T09 继续实施。

### GitHub 同步与汇报状态

- [总跟踪 Issue #12](https://github.com/BUG423/other-plc-idf/issues/12)。已建立 11 个任务 Issue（#1–#11）与 M0/M1/M2/M3/RX 五个里程碑；逐项链接在 [tasks.json](tasks.json)。T01/T02 为本轮交付，待代码合并；其余任务开放，研究任务已有首轮证据，尚无原生后端原型验收。
- `software-tests.yml` 提供源码树软件 CI，独立 wheel 验收仍为 T03 未完成项。
- `progress-report.yml` 提供任务变动、手动触发和每周一 09:00（Asia/Shanghai）的元数据报告；定时触发可能延迟。报告以独立评论追加到总跟踪 Issue，避免覆盖人工编辑；脚本已用真实 GitHub API 发出首份报告。
- 工作流当前在本工作分支提交，**尚未合并默认分支，因此定期工作流未启用**。本轮没有创建 ChatGPT 后台任务，没有持续研发代理；已发出的报告不代表未来会自动完成技术任务。

### 后续依赖与边界

- 本轮完成范围是 M0、首轮研究及 CI/报告机制的准备；不代表整个 PLC 工具链已交付。
- M1：独立安装与依赖封装、工程模型和检查、能力/JSON 接口。
- M2：正确/错误编译的真实 Windows 证据、源码同步、产物/部署契约和设备互斥。
- M3：网口可达、现场工程映射、实验机身份、逐次下载/运行操作授权和用户在场。
- 没有持续运行的研发代理。GitHub 定期报告只根据任务状态生成摘要；工作流在默认分支启用后运行，不能替代新的测试和硬件验收。
