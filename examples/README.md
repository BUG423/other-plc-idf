# examples — 示例工程区（占位）

> **状态：占位。** 本区暂无可运行示例；今天的可运行内容是 [wsl-tools/README.md](../wsl-tools/README.md)
> 里的命令（虚拟从站 + 只读监测 + 测试），请先看那里。

本区将存放**端到端、可复制修改**的示例工程，规划如下（随对应能力落地逐个补充）：

| 规划内容 | 依赖能力 | 预计形态 |
|---|---|---|
| `virtual-plc-scenarios/` 虚拟从站场景定制 | 已具备 | 基于 `wsl-tools/virtual_easy521.py` 的自定义场景脚本（改 D0~D17 播放曲线） |
| `monitor-jsonl-analysis/` 监测数据分析 | 已具备 | 读取 monitor 落盘 JSONL，画 Vpu/SOC/过载时序图的独立小脚本 |
| `serial-rs485-monitor/` RS485 口监测 | 已具备（需 USB-485 适配器硬件） | `--transport serial` 端到端示例与参数备忘 |
| `bridged-build-flash/` 桥式编译+烧录编排 | P2 GUI 桥接 | `autoshop_ui.ps1` 驱动 AutoShop 完成 build→download 的完整编排示例 |
| `native-usb-probe/` 原生 USB 协议探针 | P3 抓包逆向 | 基于逆向帧结构的最小握手/读示例 |

约定：

- 每个示例一个子目录，自带 README（怎么跑、预期输出、边界条件）；
- 示例一律遵守仓库根 README 的**安全红线**：对真机只读，写操作/下载不出现在示例默认路径中；
- 示例只 import 契约模块 `wsl-tools/e521_regs.py`，不复制粘贴寄存器定义。
