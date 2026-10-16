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
| 需求 | `requirements/` | 🚧 建设中 | 项目需求与验收标准（今日内补齐） |
| 架构 | `architecture/` | 🚧 建设中 | 桥式/原生后端架构、CLI 命令面设计（今日内补齐） |
| 协议逆向研究区 | [research/README.md](research/README.md) | 📦 框架就绪，**P3 启用** | 抓包记录、帧结构分析、发现日志、ADR 决定记录的存放地与使用规则 |

侦察档案（wsl-feasibility/00~03）为原地产物：**只增不改**；修正以新条目或附录形式追加。

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
