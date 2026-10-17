# docs — 文档总导航

本目录是 PLC-IDF（曾用名 py2plc）的全部成文知识的入口。按「侦察 → 需求 → 架构 → 研究」分阶段组织；
仓库其余专题文档（工具链、PLC 工程）见文末「docs 之外的文档」。

> 本项目曾用名 py2plc，历史档案沿用原名。

## 导航总表

| 分区 | 路径 | 状态 | 一句话 |
|---|---|---|---|
| WSL 可行性侦察（总纲） | [wsl-feasibility/00-synthesis-and-roadmap.md](wsl-feasibility/00-synthesis-and-roadmap.md) | ✅ 定稿 2026-10-16 | **先读这篇**：三条能力链可行性结论速览、关键事实、已建资产清单、P1~P4 路线图与安全红线 |
| WSL 可行性侦察 · 协议 | [wsl-feasibility/01-protocol-facts.md](wsl-feasibility/01-protocol-facts.md) | ✅ 定稿 2026-10-16 | 官方手册硬事实：Modbus TCP 从站映射（D0-D7999 ↔ FC03）、USB CDC 端点拓扑、串口/以太网规格，全部标注页码出处 |
| WSL 可行性侦察 · 生态 | [wsl-feasibility/02-opensource-ecosystem.md](wsl-feasibility/02-opensource-ecosystem.md) | ✅ 定稿 2026-10-16 | 联网开源生态调研：四条通道公开程度分级评级、autoshop-mcp 等可借鉴项目逐仓核实、可信度标注 |
| WSL 可行性侦察 · 实测 | [wsl-feasibility/03-wsl-probe-results.md](wsl-feasibility/03-wsl-probe-results.md) | ✅ 定稿 2026-10-16 | WSL 环境只读实测：WSL→Windows 被防火墙静默丢弃、代理 TUN「假连接」陷阱、USB/RTU 探测执行与红线遵守记录 |
| 需求 | [requirements/SPEC.md](requirements/SPEC.md) | v0.2 设计草案，含 2026-10-17 勘误 | 功能与验收目标；多数尚待实现 |
| 架构 | [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | v0.2 设计草案，含实现边界说明 | 后端分层与接口设计；签名草案不等同于现有 API |
| 实施与验收 | [management/PLAN.md](management/PLAN.md) | 2026-10-17 执行基线 | 第一性原理需求、任务依赖、里程碑、分工与完成定义 |
| 进展与任务 | [management/PROGRESS.md](management/PROGRESS.md) / [tasks.json](management/tasks.json) | 随里程碑更新 | GitHub 同步、测试结果、未完成与现场依赖 |
| 最新现场验证 | [management/LIVE_VALIDATION_2026-10-17.md](management/LIVE_VALIDATION_2026-10-17.md) | 2026-10-17 | USB 真机识别、CLI 正负编译、网口断开及下载/运行待验收 |
| 独立分发 | [management/DISTRIBUTION.md](management/DISTRIBUTION.md) | 已实际验收 | sdist、wheel、运行依赖与仓库外安装验证 |
| 架构审计 | [reviews/ARCHITECTURE_REVIEW_2026-10-17.md](reviews/ARCHITECTURE_REVIEW_2026-10-17.md) | 2026-10-17 | 需求/实现追溯与优先级 |
| 实现审计 | [reviews/IMPLEMENTATION_AUDIT_2026-10-17.md](reviews/IMPLEMENTATION_AUDIT_2026-10-17.md) | 2026-10-17 | 真实软件能力、P0 修复、回归及独立打包缺口 |
| 协议与固件研究 | [research/README.md](research/README.md) | 公开/离线研究已有结果；真机抓包未启动 | 四路线比较、ARM ELF 证据与历史结论勘误 |

侦察档案（wsl-feasibility/00~03）为原地产物：**只增不改**；修正以新条目或附录形式追加。

阅读顺序：先读实施计划和两份评审，再按需查看需求/架构目标及历史证据。历史调查中的“全网零实现”“RTU 完全证伪”“USB 自述确认芯片”等表述已由 [2026-10-17 复核](research/firmware/03-feasibility-assessment-2026-10-17.md) 限定证据强度，不作为当前确定事实。

## docs 之外的文档

| 文档 | 内容 |
|---|---|
| [wsl-tools/README.md](../wsl-tools/README.md) | 虚拟 Easy521 + 只读监测工具链详解（命令、参数、测试、切真机指南） |
| [plc/README.md](../plc/README.md) | BESS 储能算法资产总览与验证结论 |
| [plc/easy521/README.md](../plc/easy521/README.md) | Easy521 移植工程说明（ST 源码、AutoShop 三工程、导入表） |
| [plc/easy521/HARDWARE_COMMISSIONING_REPORT.md](../plc/easy521/HARDWARE_COMMISSIONING_REPORT.md) | 真机硬件调试报告（含 USB 下载通道验证） |
| [plc/easy521/evidence/](../plc/easy521/evidence/) | 硬件验收证据：编译/下载/监控截图与回归清单（只增不改） |
| [wsl-probe/](../wsl-probe/) | USB/RTU/网络探测脚本与原始数据（只增不改） |
| [../README.md](../README.md) | 仓库门面：定位、状态板、快速开始、安全红线 |
