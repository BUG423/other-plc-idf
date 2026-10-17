# Easy521 应用下载、供电与运行联调记录

本轮已在用户连接的 Easy521 上完成 USB 应用下载，并取得独立的厂商程序一致性通过结果。CPU 切换 RUN 被拒绝，尚未完成实际扫描执行与规则变化验收。下载成功不能写成设备运行成功。

## 现场条件与供电

用户确认：只有 Type-C 供电，没有外接 24V；输出端子没有连接电机或继电器等负载；物理 RUN/STOP 开关位于 RUN；在 RUN 灯核对之后回复灯常灭。

本机厂商用户手册 `Easy系列可编程逻辑控制器用户手册.PDF` SHA-256 为 `e793971a545f5b4c89e067957ac73973546f6df354f586a62c041995df5dc5da`：

- 第 139 页将主电源列为 24VDC ±10%，即 21.6–26.4V，并将 Type-C 列作用户程序上传/下载接口。
- 第 138 页明确讨论只有 USB 供电时不支持掉电保持。
- 在已检索的用户/编程手册和本机文本资源中，没有找到 USB-only 禁止 CPU RUN 的明确条款。因此，额定主供电尚未满足是现场事实，缺少 24V 是否为本次 RUN 拒绝的唯一原因仍未知。

实际接线需要用户按设备铭牌和手册完成，软件不能替代供电。没有将用户询问是否需要 24V 当作已连接电源。

## 实际设备操作与结果

目标由 USB BUSID `6-3`、VID:PID `0471:a531` 同时选择；厂商设备查询确认 Easy521、固件 `6.11.0.1`。当前使用 AutoShop `4.12.0.2`，工作副本为 `E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV`。原 `E:\PLCProjects\BESS_E521_DEV` 没有作为实验编辑目标。

已有设备上载 ZIP：`E:\PLCProjects\PLC_USB_BACKUP_20261017_1845.zip`，62 文件，SHA-256 `1672b4d50cd2cb8b985a2c220c87ed175a125ec0a78f395e6f4523bde6d5b386`；没有把取得备份等同于恢复演练通过。

| 实验 | 实际结果 | 证据 |
|---|---|---|
| 内存探针 revision 1 编译 | 0 错误、产生新的 ARM 对象及完整应用产物 | `probe1-build.stdout.json` |
| USB 下载应用 | 22:44:13–14，厂商日志报告下载成功 | `144426685899-probe1-download-log.json` |
| 下载后独立程序校验 | 厂商弹窗和日志均报告当前工程与 PLC 程序一致 | `144505068863-probe1-compare-terminal.json`、`144506258919-probe1-compare-log.json` |
| 在线变量读取 | 从原生表格“当前值”列读取 19 个全局量，探针值保持 0/OFF；不把“初始值”当在线值 | 在线读取记录及 `autoshop_online.py` |
| RUN 请求 | 22:46:55、22:47:19 两次均被厂商拒绝；此后未继续重试 | `144733694049-probe1-runtime-log.json` |
| 故障诊断 | 界面报告故障数量 0；不能据此确定 RUN 拒绝原因 | `144846850232-fault-diagnosis-state.json` |
| revision 2 编译后、下载前校验 | 明确报告 ST 程序不一致，证明校验确实能检出本次逻辑差异 | `150222815621-compare-negative-dialog.json` |
| revision 2 生产 CLI 下载 | 确认按钮只发一次；23:08:46–47 实际下载成功。CLI 因输出窗格替换返回 unknown；没有自动重放，后续只读对账 | `production-flash.stdout.json`、`150900127646-production-download-reconcile.json` |
| revision 2 下载后独立校验 | 23:09:45 厂商日志和精确弹窗均报告一致 | `150959610725-probe2-compare-terminal.json`、`151000842930-probe2-compare-log.json` |
| 生产 CLI USB 快照 | `values_read=true`、12 个探针量为 0/OFF；连接/CPU 证据不足，保持 unverified、退出 1 | `production-monitor.stdout.json` |

相对证据路径均位于 [`evidence/easy521-application-20261017/`](../../evidence/easy521-application-20261017/)。逐次操作记录包含时间、argv、退出码和原始输出；有副作用的动作没有自动重放。设备应用是探针，不是原 BESS 算法；最终设备与工作副本状态见该目录的验收摘要。

收尾时设备保留 **revision 2 内存探针**，没有恢复 BESS 应用，没有追加 RUN 请求。AutoShop 工作副本 MAIN 为相同 revision 2、已保存并退出在线监控；备份仍保留。适配器的 20 份不可变动作证据另存入 `adapter-records/`，原路径到保留路径的 SHA-256 对照在 `adapter-records-index.json`。

## 软件实现

- `NativeDeploy` 将完整 verified 编译记录、源/产物 SHA-256、精确工程、USB 双重身份、已上载备份和现场事实绑定。预览不下载；执行只调用一次下载，分别记录厂商完成结果、独立一致性和 CPU 状态。
- 原生 ST 编辑器在线时为只读。同步在写入或编译前拒绝，避免把在线注释中的控制字符当源程序。
- 专用只读 MSAA 表格读取器按列名解析当前值；没有 M8000/M8001 的有效在线状态证据时，CPU 状态保持 `unverified`。状态栏颜色、按钮返回值、全零变量或物理开关位置都不是 CPU 扫描证明。
- 内存探针复用现有全局变量，不写 X/Y/D/M、不调用 BESS 功能块或通信指令；revision 1/2 用于后续实际逐扫描规则变化测试。它临时改变了这些变量的业务含义。

厂商“程序校验”入口会自动再次编译，本轮实测重新生成的 `MAIN.o`/`stprog.o` 与先前清单哈希不同。已有清单应因此失效，不能静默重签或据一致性弹窗伪称精确二进制链已验证；最终自动部署联测结果单独记录。

F8 下载也实际删除 `steps.dat`、改变 `MAIN.o`/`stprog.o`。因此还有软件层面的二进制追溯缺口，不是只补电源即可把所有验收标为通过。此次生产 CLI 预检通过，下载阶段先因编译窗格→通讯窗格替换而保守返回 unknown；后续独立读取证明下载成功和程序一致。日志时窗修复只处理判定窗口，不放宽源/产物完整性。原始 unknown 记录保留，不被修复后的代码覆盖。

源码说明和调用示例见 [Easy521 CLI](../EASY521_CLI.md)。独立 sdist→wheel→全新 venv→仓库外资源/CLI/软件场景检查通过，记录在 [`distribution-easy521-20261017.json`](evidence/distribution-easy521-20261017.json)；其中 `hardware_accessed=false`，设备证据另列在上表。

最终合并软件回归为 788 passed、51 skipped、1 deselected，另 499 个 subtest 通过；51 项真实工具链用例默认跳过，真实 USB 枚举用例排除。本批没有重跑此前 Linux 工具链完整实测，远程 CI 将在同一提交重新验证；本地分项用例与合并结果有重叠。

## 无界面编译研究

已找到并实测厂商 STLang 的宿主 IPC 调用方式。两个本机编译器版本均在隔离副本中、没有 AutoShop GUI 或设备访问的情况下生成 ARM 可重定位对象。可复用 worker、错误诊断优先规则和 31 个离线回归已交付，见 [研究报告 10](../research/firmware/10-stlang-headless-compiler-2026-10-17.md)。

这个结果只覆盖冻结的厂商 `STTemp/MAIN.st` 容器到 ARM `.o`；普通 IEC 文本预处理、全工程链接/包装、Linux 原生厂商编译器和脱离 AutoShop 的设备协议尚未打通。它没有确认实际 SoC、操作系统、启动链或 BSP，也没有执行固件替换。

## 剩余验收

补充额定 24V 主电源之后，需重新读取设备状态并核对输出隔离，再验证 RUN、实际计数/心跳变化、停止与 revision 1→2 的运行结果变化。物理 I/O、掉电保持、真实时间精度、网口部署、恢复演练和多型号硬件仍未验收。T09/T08/R01 保持开放；既有开放 Linux 软件闭环不替代这些设备证据。
