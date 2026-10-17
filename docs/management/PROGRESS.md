# 进展记录

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
