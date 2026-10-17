# ⚡ PLC-IDF

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-WSL2%20%2B%20usbipd--win-0078D6?style=flat-square&logo=linux&logoColor=white)](https://learn.microsoft.com/windows/wsl/about)
[![Protocol](https://img.shields.io/badge/Protocol-Modbus%20TCP%20%2F%20RTU-orange?style=flat-square)](https://www.modbus.org/)
[![Target PLC](https://img.shields.io/badge/Target%20PLC-Inovance%20Easy521-9370DB?style=flat-square)](https://www.inovance.com/)
[![Software tests](https://img.shields.io/badge/Software%20Tests-346%20passed-brightgreen?style=flat-square)](docs/management/LIVE_VALIDATION_2026-10-17.md)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm--Noncommercial--1.0.0-red.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0)

> **PLC-IDF** 面向 AI 与终端开发，对标 ESP-IDF 的项目、构建、下载和监测工作流。当前为汇川 Easy521 工具链原型：文本工程与监测软件链可用，已有 Windows 工程的 MAIN 同步及编译通过正负实测，下载和真机运行仍待验收。

2026-10-17 最新实测：[现场验证报告](docs/management/LIVE_VALIDATION_2026-10-17.md) · [实施计划与验收](docs/management/PLAN.md) · [进展](docs/management/PROGRESS.md) · [基线实现审计](docs/reviews/IMPLEMENTATION_AUDIT_2026-10-17.md) · [底层固件复核](docs/research/firmware/03-feasibility-assessment-2026-10-17.md)。已通过 USB 确认 Easy521/6.11.0.1，并在独立 Windows 工程副本完成 CLI 正负编译验证；网口物理链路仍断开，下载及运行未验收。

后续方向已扩展为 **原生终端、多目标 PLC 工具链**：Easy521 是首个兼容目标，AutoShop 桥接保留；并行研究厂商运行时的 ABI/编程协议，以及自控 Linux 目标上的开放 ST 运行时。共享接口与独立验收见 [ADR-0002](docs/architecture/ADR-0002-native-multitarget.md)，不承诺不同 PLC 共用系统固件。

---

## 🌟 项目简介

**PLC-IDF** 的目标是在 WSL 内完成「文本工程 → 编译 → 应用下载 → 运行反馈」。先适配 Windows AutoShop 桥接，再按证据评估原生后端。目标设备为汇川 Inovance **Easy521-0808TN**；历史固件记录为 6.11.0.1，已有 AutoShop 4.11/4.12 适配记录，当前版本须在联调时核实。现有 USB 通道可供 Windows 厂商工具使用，真机网口监测仍待验收。

- 🏷️ **曾用名**：项目曾用名 **py2plc**，已更名 **PLC-IDF**（包名 `plc_idf`，命令名 `plcidf`）；`py2plc` 命令保留为等价别名。历史档案（`docs/wsl-feasibility/`、`wsl-probe/`、`wsl-tools/` 等）沿用原名，保留原貌。
- ⚙️ **CLI 状态**：已有 `new / init / check / doctor / sync / build / flash / monitor / sim / devices`。`sync` 仅对已有原生工程 MAIN 正文做 diff/读回同步；`build --source FILE --working-project WINDOWS_PATH` 校验新产物、ARM ELF、源文本及厂商 0 错误日志。现场正负测试分别返回 verified/0、failed/1。普通 `build/flash` 仍保留旧 GUI 计划，结果校验不全时不会报成功；下载未完成设备端验收。
- 📦 **分发**：独立 wheel 已包含运行脚本、寄存器契约和 Windows 桥接脚本，自动安装必要依赖；已验证从 sdist 构建、在新 venv 与仓库外运行三个软件场景，见 [分发说明](docs/management/DISTRIBUTION.md)。
- 🧪 **验证边界**：软件 CI 与真机验收分开；场景播放器不执行用户 ST，Modbus 数采也不等同于断点/单步。校准无法判别时退出 3，JSONL 标明字序来源、原始值、质量与尚未验证的程序匹配/扫描进度。

---

## 🖼️ 架构与能力链总览

```mermaid
flowchart LR
    DEV["👨‍💻 开发者<br/>WSL2 终端"] --> CLI["⚡ plcidf CLI<br/>new / check / doctor / sync / build<br/>flash / monitor / sim / devices"]

    CLI --> SIM["🧪 虚拟 Easy521 从站<br/>127.0.0.1:1502"]
    CLI --> MON["🔍 监测链 · ✅ 软件闭环<br/>只读 monitor + REAL32 字序校准"]

    subgraph BRIDGE["桥式后端（Windows）"]
        BUILD["🏗️ build --source · MAIN 同步与新产物校验<br/>✅ AutoShop 4.12 正负测试已通过"]
        FLASH["📦 下载链 · 厂商路径有历史人工证据<br/>⚠️ CLI 下载结果校验待实现"]
        AUTO["🪟 AutoShop<br/>GUI 驱动（autoshop_ui.ps1）"]
        BUILD --> AUTO
        FLASH --> AUTO
    end

    USB["🔌 USB 传输层<br/>当前由 Windows 厂商驱动使用<br/>历史已验证 usbipd attach"]
    HW["🎛️ 汇川 Easy521-0808TN<br/>Modbus TCP 502 / USB CDC"]

    MON --> HW
    AUTO --> HW
    USB --> HW

    style CLI fill:#eef2ff,stroke:#6366f1,color:#1e1b4b
    style MON fill:#f0fdf4,stroke:#22c55e,color:#14532d
    style USB fill:#f0fdf4,stroke:#22c55e,color:#14532d
    style BUILD fill:#fff7ed,stroke:#f97316,color:#7c2d12
    style FLASH fill:#fff7ed,stroke:#f97316,color:#7c2d12
    style AUTO fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    style HW fill:#fef2f2,stroke:#ef4444,color:#7f1d1d
```

---

## 📊 项目状态板

| 能力链 | 当前状态 | 路线 |
| :--- | :--- | :--- |
| **监测链** | ✅ 虚拟从站、只读数采及字序判别软件测试通过；15 组历史 USB RTU 参数下均无应答，尚无可用 USB RTU 监测证据；⏳ 真机网口待验收 | **T08** 网口已知值对照、字序和质量验证；请求校准但无法判别时退出 3，不回落默认值 |
| **编译链** | ✅ 已有工程 MAIN 正文同步、完整日志与新产物校验，CLI 正负测试通过；当前仍依赖 Windows，尚无自动变量/原生工程创建 | **T06** 继续补变量/任务同步及版本覆盖；H3U DLL 线索不能证明 Easy521/LiteST 原生编译已可用 |
| **应用下载链** | 历史 AutoShop 人工下载有成功及程序一致性证据；当前 CLI 未实现设备端结果校验，授权后调用链仍可能产生实际下载 | **T07/T09** 先做产物/目标绑定、设备互斥，再做现场下载与一致性验收；**R01** 独立研究原生下载 |
| **USB 传输层** | ✅ usbipd 挂载打通，内核 cdc_acm 认领 `/dev/ttyACM0`（2026-10-16 实测） | 维持 usbipd attach/detach 工作流（与 AutoShop 使用互斥） |

本轮真实网卡状态为 `Disconnected / 0 bps`，设备在 Windows 侧；不把旧 USB attach 记录或虚拟监测当作当前网口实测。当前编译工作副本与复现命令见现场报告。

当前任务顺序与验收以 [实施计划](docs/management/PLAN.md) 为准。旧 P1–P5 编号保留在 [历史可行性档案](docs/wsl-feasibility/00-synthesis-and-roadmap.md)，其中过强结论的勘误见最新研究报告。

---

## 🚀 快速上手

### 环境要求

- Windows 10/11 + **WSL2**（Ubuntu）；对真机做 USB attach 时需要 **usbipd-win**（当前监测演示不需要 USB）。
- Python 3.12 虚拟环境（仓库根 `.venv/`）：`pymodbus 3.16.1`、`pyserial`、`pytest`。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-ci.txt
.venv/bin/pip install -e .        # 安装 PLC-IDF CLI（plcidf 命令 + py2plc 别名）
```

### 三行上手（今天就能跑）

```bash
.venv/bin/python wsl-tools/virtual_easy521.py --scenario low_voltage   # ① 起虚拟 Easy521 从站（127.0.0.1:1502）
.venv/bin/python wsl-tools/e521_monitor.py --once --calibrate --with-bits   # ② 另开终端：只读监测 + REAL32 字序自动判别
.venv/bin/python -m pytest wsl-tools/tests/ -q                          # ③（可选）监测软件测试
```

### CLI 一键闭环（零硬件）

```bash
plcidf sim --with-monitor --once            # 虚拟从站 + 只读监测一轮（封装上面两步）
plcidf build                                # AutoShop 编译管线 dry-run（打印调用链，不执行）
plcidf devices                              # USB 设备在线状态表
plcidf monitor --once --calibrate           # 只读监测（封装 wsl-tools/e521_monitor.py）
plcidf --help                               # 全部子命令与参数
```

新增文本工程、检查与诊断：

```bash
plcidf new /tmp/my_plc_project --json
plcidf check /tmp/my_plc_project --json
plcidf doctor --json
```

脚手架只生成 ST/变量/映射文本与导入表，不直接生成厂商 `.hcp`。已有 Windows 工程的 MAIN 同步与真实编译命令见 [现场复现](docs/management/LIVE_VALIDATION_2026-10-17.md)。

配置可选：`cp .plcidf.toml.example .plcidf.toml` 后按需改；所有键都有内置默认值（`bridge.enabled` 默认 false）。也可用 `--config FILE` 或环境变量 `PLCIDF_CONFIG` 指定。

### 虚拟 PLC 演示（wsl-tools 原生用法）

见上文「三行上手」。三个场景可换：`steady`（稳态）/ `low_voltage`（低压穿越）/ `overload`（过载交替），更多参数见 [wsl-tools/README.md](wsl-tools/README.md)。虚拟从站只绑 `127.0.0.1`，不出本机。

### 真机只读监测（命令模板，待 P4 网口接通）

```bash
# 首连：先 --calibrate 判 REAL32 字序（官方未记载字序，必须校准）
plcidf monitor --host <PLC_IP> --port 502 --once --calibrate

# 确认字序后持续采集
plcidf monitor --host <PLC_IP> --port 502 --interval 1 --jsonl real.jsonl

# RS485 口 + USB-485 适配器的备选模板；本机端口配置仍需现场核对
plcidf monitor --transport serial --device /dev/ttyUSB0 --once --calibrate
```

注意：真机 IP 目前无记录（拿到拓展坞插网线后用 AutoShop 搜索或扫描发现）；`<PLC_IP>` 处的 D0~D17 寄存器需先按 `plc/easy521/modbus_register_map_real32.csv` 契约映射好。

---

## 🛡️ 安全红线（硬约束，对所有协作者与 AI Agent 生效）

1. **对真机只读**：只允许 Modbus 读功能码 FC01/02/03/04。`wsl-tools/e521_monitor.py` 在代码层面锁死只读，测试有用例专门验证这一点。
2. **写 / RUN / STOP / 下载**：仅在用户**明确授权且在场**时执行；虚拟从站同样拒绝一切写功能码。
3. **下载前确认**：核对目标工程与真机备份（`plc/backups/`），确认拨码与工程目标一致。
4. **USB 与 AutoShop 互斥**：usbipd attach 到 WSL 期间 AutoShop 无法使用该设备；切换必须 attach/detach 显式操作，不抢设备。
5. **TUN 代理雷**：代理 TUN 开启时所有 TCP「假连接」（SYN 被假应答），联调与扫描数据不可信——联调前先绕开 TUN（证据 `wsl-probe/plc_discover_run2_INVALID_tun_fakeaccept.json`）。

---

## 🗂️ 目录导航

```
PLC-IDF/
├── README.md               ← 本文件（仓库门面）
├── LICENSE                 ← PolyForm Noncommercial 1.0.0（非商用许可证）
├── .plcidf.toml.example    ← CLI 配置模板（cp 成 .plcidf.toml 生效，真实配置不入库）
├── docs/                   ← 文档总导航（入口：docs/README.md）
│   ├── wsl-feasibility/        P1 侦察档案：00 总纲 + 01~03 专项（原地产物，只增不改）
│   ├── requirements/           v0.2 需求草案与实现边界勘误
│   ├── architecture/           v0.2 架构草案与实现边界勘误
│   ├── management/             实施计划、任务定义、进展
│   ├── reviews/                架构与实现质量审计
│   └── research/               协议逆向研究区（框架已建，P3 抓包阶段启用）
├── src/plc_idf/            ← CLI 本体：plcidf build / flash / monitor / sim / devices
├── wsl-tools/              ← 虚拟 Easy521 从站 + 只读监测客户端 + 寄存器契约 + 测试（可直接运行）
├── wsl-probe/              ← USB / RTU / 网络探测证据（原始数据存档，只增不改）
├── plc/                    ← BESS 储能算法与 AutoShop 工程资产
│   ├── easy521/                Easy521 移植工程：ST 源码、AutoShop 三工程（DEV/TEST/BENCH）、验收证据 evidence/
│   ├── tools/                  AutoShop GUI 驱动 PowerShell 工具（截图/控件树/点击/弹窗确认）
│   ├── tests/                  算法镜像回归（plc_mirror.py）
│   └── backups/                真机工程备份（烧录前先备份到这里）
├── examples/               ← 示例工程区（占位，见 examples/README.md）
└── （根目录历史文件区）        早期资料，保留原地，见下表
```

根目录历史文件区（早期产物，保留原地，不是当前工具链的一部分）：

| 文件 | 说明 |
| :--- | :--- |
| `AutoShop_Easy521入门教程.md` | 早期 AutoShop 入门学习笔记 |
| `PLC控制代码编写与测试报告.md` | 早期控制代码编写与测试过程报告 |
| `modbus_realtime.py` | 早期 Modbus 实时采集脚本（原型，已被 `wsl-tools/` 取代） |
| `ieee33_bess_adaptive_R2022b.slx` | IEEE-33 节点 + BESS 自适应控制 Simulink 模型（R2022b，算法出处） |
| `模型框图.docx` | 上述模型的框图说明 |

另：`.mimocode/` 为本地工具状态，不参与项目构建。

---

## 📚 文档索引

- **[docs/README.md](docs/README.md)** — 文档总导航（从这里进）
- `docs/wsl-feasibility/`（P1 侦察档案）
  - [00-synthesis-and-roadmap.md](docs/wsl-feasibility/00-synthesis-and-roadmap.md) — 总纲：可行性结论速览、关键事实、已建资产、P1~P4 路线图
  - [01-protocol-facts.md](docs/wsl-feasibility/01-protocol-facts.md) — 官方手册硬事实（Modbus TCP 映射、USB CDC 拓扑、串口规格，页码可溯）
  - [02-opensource-ecosystem.md](docs/wsl-feasibility/02-opensource-ecosystem.md) — 开源生态联网调研（各通道公开程度评级、可借鉴项目）
  - [03-wsl-probe-results.md](docs/wsl-feasibility/03-wsl-probe-results.md) — WSL 环境实测（防火墙阻断、TUN 假连接、只读探测执行记录）
- [需求草案](docs/requirements/SPEC.md) 与 [架构草案](docs/architecture/ARCHITECTURE.md) — 包含计划能力，卷首明确实际实现边界
- [实施计划](docs/management/PLAN.md) 与 [进展](docs/management/PROGRESS.md) — 当前执行入口
- `docs/research/` — 已有公开资料和离线产物研究；真机协议抓包尚未启动，见 [研究导航](docs/research/README.md)
- 专题文档：[wsl-tools/README.md](wsl-tools/README.md)（工具链详解）、[plc/README.md](plc/README.md)（BESS 算法资产）、[plc/easy521/README.md](plc/easy521/README.md)（Easy521 工程与硬件验收）

---

## 🤖 面向 AI Agent 的导航提示

- 📖 先读 [docs/README.md](docs/README.md) 与 `docs/wsl-feasibility/00`，再动手；寄存器契约唯一定义在 `wsl-tools/e521_regs.py`，不要在别处另立字序/偏移定义。
- 🔒 证据只增不改：`wsl-probe/`（探测原始数据）与 `plc/easy521/evidence/`（硬件验收截图）不许修改、删除或重命名。
- 🏷️ 历史档案（`docs/wsl-feasibility/`、`wsl-probe/` 等）里的「py2plc」是本项目曾用名，指的就是 PLC-IDF，不要改写历史文件。
- 🛡️ 上面「安全红线」是硬约束，不因任务紧急而放宽。

---

## 📄 知识产权与开源协议

本项目遵循 **[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)** 非商业开源许可（完整文本见 [LICENSE](LICENSE)）：

- ❌ **严禁商用**：仅限个人学习、研究、非营利组织等**非商业目的**使用；任何商业盈利目的的使用需另行联系维护者获得商用授权。
- 📦 **第三方组件各自许可**：依赖的第三方组件（pymodbus、pyserial 等及各 AutoShop/PLC 资产的来源）**各自保留其原始许可证**，本许可证不覆盖它们。
- 🏷️ **曾用名说明**：历史档案中出现的 **py2plc 为本项目曾用名**（现名 PLC-IDF），档案按原貌保留。
