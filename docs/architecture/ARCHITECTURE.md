# PLC-IDF 架构设计（ARCHITECTURE）

> **多目标方向更新（2026-10-17）**：用户要求将去 Windows 与多 PLC 适配作为产品方向。新增 [ADR-0002](ADR-0002-native-multitarget.md)，明确工程模型、编译/部署/运行时/传输/BSP 的边界与版本化迁移；Easy521 兼容研究和开放运行时分别验收。下文 Windows 路径及 USB 固定签名为旧草案，不再作为未来所有后端的共同约束。

> **后续实装更新（2026-10-17）**：已增加文本工程与离线诊断、MAIN 同步/可信编译适配、产物/计划记录、进程租约和严格证据模块，见 [现场验证](../management/LIVE_VALIDATION_2026-10-17.md)。首次审计与下文签名草案仍有未实现项；新模块名称及实际 API 以源码和最新进展为准。设备端下载/运行验证、自动变量/任务同步和真机网口验收尚未完成。

| 项 | 值 |
|---|---|
| 文档编号 | PLC-IDF-ARCH-001（原 py2plc-ARCH-001，随 v0.2 更名） |
| 版本 / 状态 | v0.2 · 设计评审稿（2026-10-16；2026-10-17 勘误） |
| v0.2 变更 | ① 更名 PLC-IDF（包 `plc_idf`，CLI `plcidf`，`py2plc` 兼容别名），与 ESP-IDF 升级为**命名对标**（§10）；② 新增 §3 源语言层（IEC 61131-3 ST）与 §6.5 工程脚手架/同步接口；③ 新增 §4 设备开放监控接口事实基线；④ §11 演进矩阵新增 **P5 底层/固件研究轨**（含安全红线）；⑤ §8 授权策略表新增 FIRMWARE 预留档 |
| 需求来源 | [需求规格说明书](../requirements/SPEC.md)（FR/NFR 编号在本文直接引用） |
| 事实依据 | [00 总纲](../wsl-feasibility/00-synthesis-and-roadmap.md) · [01 协议](../wsl-feasibility/01-protocol-facts.md) · [02 生态](../wsl-feasibility/02-opensource-ecosystem.md) · [03 实测](../wsl-feasibility/03-wsl-probe-results.md) · `wsl-probe/usb-rtu/` |
| 读者 | `src/plc_idf/` 实现者（接口签名草案即编码契约）、评审者 |

> **2026-10-17 勘误与实现边界**：本文件保留 v0.2 目标架构，图、接口、状态机和命令表均不代表已全部实现。当前 CLI 为 `build / flash / monitor / sim / devices`；工程脚手架、同步、静态检查、后端注册表、设备租约、计划哈希授权与证据查询等尚未交付。现有 ABC 与本文件 Protocol 签名并不一致；迁移应分任务验收。虚拟从站不执行用户 ST，GUI 编排也未完成厂商编译及下载一致性验收。
>
> 需求—实现—验收矩阵和 P0 修复边界见 [2026-10-17 架构评审](../reviews/ARCHITECTURE_REVIEW_2026-10-17.md)。当日 strict bool、GUI 动作不重试及未校验结果不报成功三项修复已落地并通过软件集成验证，结果见 [进展记录](../management/PROGRESS.md)；这不代表真实编译和下载校验已实现。当前对原生编译、USB 协议与底层平台的结论是“尚待目标验证”，不是“不可能”；ARMv7-A/ELF 产物等新线索见 [底层可行性复核](../research/firmware/03-feasibility-assessment-2026-10-17.md)。

> **命名与兼容**：产品名 **PLC-IDF**，Python 包 **`plc_idf`**，CLI 命令 **`plcidf`**；`py2plc` 保留为兼容别名（仓库物理目录名暂保留）。行文统一用 `plcidf` 指代命令。
>
> **引用约定**：`[SPEC-FR-x]` = 需求文档条目；`[00-K#]` `[01-X#]` `[02-#.#]` `[03-①]` `[RTU]` 与 SPEC 相同（见 SPEC 卷首）。本文代码块为 **Python 3.12 签名草案**，实现侧应以 `src/plc_idf/backends/base.py` 等文件落地，签名可细化、语义不可偏离。

---

## 1. 架构总览

### 1.1 需求与当前证据 → 七条架构原则

| # | 第一性结论（事实依据） | 架构原则 |
|---|---|---|
| 1 | 用户需要 WSL/终端内的开发闭环：统一 CLI、减少 GUI 操作、数据回流和可替换后端；去 Windows 化作为独立部署目标评估（SPEC §1.2） | **P1 命令面稳定**：后端替换保留核心动词，同时如实报告平台依赖和能力差异 |
| 2 | 当前选用厂商 Windows 编译工具；尚无经本目标验证的原生编译/下载后端，历史检索不能证明其他路线不存在（SPEC §2.2） | **P2 桥式先验证、后端可替换**：为编译、下载和监测分别定义能力边界，按证据评估后续替换 |
| 3 | 首期选 ST：文本审查直接，已有 ST+CSV→AutoShop 人工导入资产；图形语言的生成与语义比较并非不可能，只是本项目未实现（SPEC §2.1） | **P3 文本工程 + 数据面先行**：先形成可审查源工程；虚拟数采与用户 ST 执行分别验收 |
| 4 | 监控协议开放（Modbus TCP [01-A3]）而编译/下载协议私有 [02-1.4]——不对称是事实（[ARCH-§4]） | **P4 分层消解不对称**：监测走原生开放协议，编译/烧录走桥式，各自独立演进 |
| 5 | 红线：真机默认只读；写/RUN/STOP/下载须明确授权且在场 [00-六]；固件级实验更须单独授权（P5） | **P5 默认拒绝**：安全门禁在 CLI 层统一实施，后端只接受签发的授权凭据，不自查旗标 |
| 6 | USB 与 AutoShop 互斥；TUN 假连接；Hyper-V 防火墙 [00-K5] [03-①] | **P6 环境敌意假设**：设备所有权由单一管理器调度；一切网络连接默认可疑，须过防假校验 |
| 7 | GUI 驱动脚本不应作为无人监督的硬件部署脚本 [01-E2] | **P7 一切副作用留证据**：证据是一等输出物，不是日志副产品 |

### 1.2 一句话架构

**目标架构：一个稳定的 CLI 命令面（`plcidf`），连接文本 ST 工程与 Build / Flash / Monitor 三类后端；首期验证 Windows 厂商桥接和原生 Modbus 数采，后续依证据替换后端；安全门禁、设备互斥和证据贯穿副作用操作。**

---

## 2. 分层架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 用户层：人类开发者(U1) ｜ AI Agent(U2, 一等用户)                                │
│         plcidf init/new ｜ sync ｜ build ｜ flash ｜ monitor ｜ sim            │
│                    ｜ device ｜ evidence ｜ backends                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 源语言层（IEC 61131-3 ST，首期选定前端 [ARCH-§3]）                             │
│   *.st 正文  ｜  *_variables.csv 变量表  ｜  modbus_register_map_real32.csv 契约│
│   （可 diff / 可 review / 可 AI 生成；编译语义不在本层 [SPEC-FR-L4]）             │
├─────────────────────────────────────────────────────────────────────────────┤
│ CLI 层  src/plc_idf/cli.py                                                    │
│   子命令解析 · --json 输出 · 退出码(NFR-REL2) · --plan 两步授权协议(FR-SEC5)     │
├─────────────────────────────────────────────────────────────────────────────┤
│ 横切层（对所有后端生效）                                                        │
│   safety.py  授权门禁 SafetyGate：策略表→旗标+在场+确认短语→Authorization 令牌   │
│              证据写入（append-only JSONL + 截图引用）     [SPEC-FR-E*]          │
│   device.py  设备管理器 DeviceManager：USB 所有权状态机 + 租约锁  [ARCH-§9]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 后端接口层  backends/base.py（Python Protocol + 数据类，[ARCH-§6]）             │
│   BuildBackend        FlashBackend         MonitorBackend/MonitorSession      │
│  （首期 AutoShop       （默认拒绝的插槽）      （硬编码只读 [SPEC-FR-M1]）        │
│    [SPEC-FR-L4]）                                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ 后端实现层（同一插槽、可替换实现，[ARCH-§11] 替换矩阵）                           │
│   bridge_autoshop   编译+烧录+工程同步（桥式）   monitor_modbus   监测（原生网口） │
│   sim_virtual       监测+静态检查              native_usb      预留（P3 后评估） │
├─────────────────────────────────────────────────────────────────────────────┤
│ 执行环境层                                                                    │
│   Windows：AutoShop（GUI，首期编译后端）+ autoshop_ui.ps1 驱动 + usbipd          │
│   WSL：pymodbus 3.16.x ｜ virtual_easy521.py ｜ /dev/ttyACM0(attach 后)        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 目标层：Easy521 真机（Modbus TCP 502 主通道 ｜ USB CDC bulk 私有协议 [ARCH-§4]）  │
│         虚拟 Easy521（127.0.0.1:1502，契约同源 [00-四]）                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

要点：目标设计使上层通过能力接口使用桥式或原生后端，避免依赖具体执行平台。源工程层负责文本、配置和契约，编译语义由具体 BuildBackend 提供。当前 CLI 仍直接引用具体后端，注册与调度机制尚待实现；平台可替换性须经扩展验收，而不是由目录结构自动保证。

---

## 3. 源语言层：IEC 61131-3 ST

### 3.1 为什么是 ST（选型论证）

本项目首期选用 ST，是因为现有 LiteST 正文、变量 CSV、导入表和算法移植资产可复用，便于逐行审查。IL 也是文本；LD/FBD/SFC 也可以通过图模型、序列化或中间表示生成和审查，但需要验证本目标的导入、执行及语义保持能力。当前不承担这些前端，不能把范围取舍写成技术上不可能。选型表见 [SPEC §2.1]。

### 3.2 职责边界（红线）

| WSL 侧目标职责 | 首期交由 Windows/AutoShop 的职责 |
|---|---|
| `init/new`：ST+CSV 工程脚手架（FR-L1，待实现） | **厂商编译**：当前选用 AutoShop；其他后端未经本目标验证（C1） |
| 编辑：`.st` 正文 + `*_variables.csv`（人/AI） | 导入执行的最终解释（导入表格式以 AutoShop 版本为准） |
| 静态检查：LiteST 方言边界（无 `LREAL`、≤1000 行）+ 变量表↔寄存器契约一致性（FR-L3，C13） | 下载/上载/在线监控的私有协议通道（C2） |
| 虚拟仿真、监测、编排、证据 | — |

**当前没有已验证的原生 ST 编译后端**（[SPEC-FR-L4]）。不得把静态检查、Python 镜像或 GUI 按键成功宣称为厂商编译成功。原生编译是可研究的独立后端，须证明方言、ABI、链接和运行兼容性；ARM ELF 中间产物仅提供研究线索。

### 3.3 数据流（文本 → 厂商工程）

```
 *.st + *_variables.csv (WSL, 可 diff)
        │  plcidf sync（FR-L2：导入表驱动，--dry-run diff 预览先行）
        ▼
 autoshop_global_import.csv / autoshop_fb_import.csv   ← 格式已实证 [SPEC §2.3]
        │  （UTF-8 → UTF-16 转码；bridge 经 AutoShop 文件导入对话框/导入表完成）
        ▼
 AutoShop 工程（.hcp 等，厂商私有格式 [01-F]）
        │  plcidf build（bridge_autoshop 驱动 GUI 编译）
        ▼
 编译产物 → plcidf flash（授权门禁）→ 真机
```

已有资产实证该工作流：`plc/easy521/` 的两个 ST 正文、两个变量 CSV 与两份导入表"已成功导入"真实 AutoShop 4.11 工程（含 UTF-16 编码注意事项、LiteST 无 `LREAL` 的方言约束）[SPEC §2.3]。`plcidf init/new/sync` 只是把这条人工流程自动化，不引入新编译语义。

### 3.4 接口落点

脚手架（init/new）为纯本地文本操作，属 `cli.py` 职责；`sync` 触碰 Windows 侧工程文件，必须经桥式后端并走证据链——接口见 [ARCH-§6.5]。

---

## 4. 设备开放监控接口（事实基线）

回答"板子是否提供监控接口"。三档通道及其架构含义（事实详表见 [SPEC §3]）：

| 档 | 通道 | 事实要点 | 架构落点 |
|---|---|---|---|
| **主通道（开放）** | 以太网 Modbus TCP | 502 出厂默认开启、D0-D7999↔FC03 直映、不校验站号、协议三方互证 [01-A3] [02-1.1] | `monitor_modbus` 后端的真机形态（P4 起主位）；NFR-PORT5 仅换 `--host` |
| **备选（协议开放/本机待验）** | RS485 Modbus RTU | RTU 从站映射与以太网相同 [01-A3]；[手册复核](../research/firmware/01-manual-facts.md) 记录本体 1 路及端子定义 | 串行传输软件已备，本机接线与参数、实际通信仍待验收，见 O9 |
| **协议待研究** | USB 编程口 | 档案记录 CDC/bulk 拓扑，原始描述符待补 [00-K2]；项目未掌握编程协议；15 组 RTU 探测无响应，当前不支持该监测路径 [RTU] | `native_usb` 为设计预留，当前尚无实现或注册条目；先作 Windows 侧桥式资源 |

**USB 固件升级/烧录** 与应用程序下载分别处理；相关机制属 P5 底层/固件研究轨，当前只开展离线研究（[ARCH-§11]）。

设计含义（P4 原则）：监控协议开放而编程协议私有这一**不对称**，被"监测原生 + 编译烧录桥式"的分层直接消解——两半各自沿最优路径演进，互不阻塞。

---

## 5. 模块职责

目标目录与职责（包含尚未实现的命令、类和文件；当前能力见卷首及评审矩阵）：

```
src/plc_idf/
├── __init__.py
├── cli.py                      # 子命令：init/new/sync/build/run/stop/monitor/sim/device/evidence/backends
├── safety.py                   # SafetyGate / SafetyAction / Authorization / 证据写入
├── device.py                   # DeviceManager / DeviceState 状态机 / 租约锁
└── backends/
    ├── __init__.py             # 注册表 REGISTRY + get_backend()（[ARCH-§6.6]）
    ├── base.py                 # 全部 Protocol 与数据类（[ARCH-§6]），零第三方依赖
    ├── bridge_autoshop.py      # BuildBackend + FlashBackend + 工程同步（首期厂商桥接）
    ├── monitor_modbus.py       # MonitorBackend（Modbus TCP + RS485 RTU 传输）
    ├── sim_virtual.py          # MonitorBackend + 离线检查（虚拟闭环，包 wsl-tools 资产）
    └── native_usb.py           # 预留插槽（P3 抓包评估后决定，[00-五P3]）
```

| 模块 | 职责 | 明确不做 | 关联需求 |
|---|---|---|---|
| `cli.py` | 命令面、参数解析、`--json/--plan`、退出码、trace id、ProgressSink 装配；**工程脚手架 `init/new`（纯本地文本操作，模板随包分发）** | 不含业务逻辑与协议细节；**不读环境决定授权**（只透传旗标给 SafetyGate）；不实现任何编译 | [SPEC-FR-X2]、FR-L1、NFR-OBS1 |
| `safety.py` | 授权策略表（[ARCH-§8.2]）、确认短语派生、Authorization 签发与校验、证据 JSONL 追加、`evidence` 子命令数据访问 | 不执行任何操作本身；不缓存授权（FR-SEC3） | [SPEC-FR-SEC*]、FR-E* |
| `device.py` | USB 所有权状态机、租约锁落盘与陈旧检测、attach/detach 编排（调 usbipd）、设备身份建档 | 不触碰 PLC 应用状态；不做字序/量程校验 | [SPEC-FR-D*] |
| `backends/base.py` | 接口与数据类唯一定义处（对齐 `e521_regs.py` 作为契约唯一定义处的风格 [00-四]） | 不 import 任何后端实现（防依赖倒置） | [SPEC-FR-X1] |
| `bridge_autoshop.py` | 驱动 AutoShop GUI 完成编译/下载/校验（经 `autoshop_ui.ps1`：inspect/file/accept/log/screenshot [01-E2]）；owner-drawn 输出退回截图；弹窗 accept 策略；**工程同步 `sync`（导入表驱动，[ARCH-§6.5]）** | 不绕过门禁自查旗标；不做无人监督写操作（C9）；不实现编译语义（编译在 AutoShop） | [SPEC-FR-B1~B5、F1~F7、L2] |
| `monitor_modbus.py` | Modbus TCP/RTU 只读采集、`--calibrate` 字序判别、防假连接校验、表格+JSONL | **源码面禁止出现写功能码**（[ARCH-§8.3]）；不写 PLC | [SPEC-FR-M1~M5、M7] |
| `sim_virtual.py` | 包 `wsl-tools/virtual_easy521.py`（三场景）为 MonitorBackend；静态检查承载点（FR-L3） | 不声称厂商等价编译（FR-B6/L4） | [SPEC-FR-M6、S1~S3、B6、L3] |
| `native_usb.py` | P3 后：pyusb bulk 端点（0x02/0x81 [00-K2]）帧协议实验 | 本期不实现；注册表中标注"预留" | [SPEC-FR-M8]、[ARCH-§11] |

---

## 6. 后端接口定义（Python Protocol 签名草案）

### 6.1 公共数据类与 ProgressSink（`backends/base.py`）

```python
# src/plc_idf/backends/base.py —— 接口唯一定义处，仅标准库
from __future__ import annotations
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EnvironmentReport:
    backend: str
    ok: bool
    problems: list[str] = field(default_factory=list)   # 人读 + --json 双用
    details: dict[str, str] = field(default_factory=dict)  # 版本/路径等事实


@dataclass(frozen=True)
class Diagnostic:
    severity: str                 # "error" | "warning" | "info"
    message: str
    source: str                   # "autoshop-log" | "screenshot" | "static-check" | "sync-diff" ...
    hint: str | None = None


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str                   # 产物不可抵赖 [SPEC-FR-B2]
    kind: str = "firmware"        # 预留多产物


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    artifacts: list[Artifact]
    diagnostics: list[Diagnostic]
    evidence_id: str              # 证据条目 ID [SPEC-FR-E1]


@dataclass(frozen=True)
class FlashResult:
    ok: bool
    verified: bool                # 程序校验结果 [SPEC-FR-F3]
    plc_state_source: str | None  # 状态来源声明，禁止虚构 [SPEC-FR-F7]
    evidence_id: str


class ProgressEventKind(StrEnum):
    STEP = "step"          # 阶段推进
    MESSAGE = "message"    # 人读消息
    METRIC = "metric"      # 数值（监测值/进度百分比）
    EVIDENCE = "evidence"  # 证据产出（截图/日志路径）


@runtime_checkable
class ProgressSink(Protocol):
    """进度事件汇：CLI 装配，后端只调用。事件同步进证据（NFR-OBS2/3）。"""
    def event(self, kind: ProgressEventKind, message: str, /, **fields: str) -> None: ...
```

### 6.2 BuildBackend

```python
@dataclass(frozen=True)
class BuildRequest:
    project_dir: Path             # ST 源 + 变量 CSV + AutoShop 工程资产根目录
    target: str = "Easy521-0808TN"
    out_dir: Path = Path("build")
    clean: bool = False


class BuildBackend(Protocol):
    name: str                                        # "bridge_autoshop" | "sim_virtual"

    def check_environment(self) -> EnvironmentReport: ...
    def build(self, request: BuildRequest, *, sink: ProgressSink) -> BuildResult:
        """编译。首期选定厂商语义后端为 bridge_autoshop（C1/SPEC-FR-L4）；
        sim_virtual 只做静态检查/离线验证包。调用前 CLI 已取得 WINDOWS_AUTOSHOP 租约；
        实现内部只驱动 AutoShop，不做授权判断（门禁在 safety.py）。"""
```

### 6.3 FlashBackend

```python
class SafetyAction(StrEnum):   # 实际定义于 safety.py，此处为语义引用
    READ = "read"; ATTACH = "attach"; DETACH = "detach"
    WRITE = "write"; FLASH = "flash"; RUN = "run"; STOP = "stop"
    FIRMWARE = "firmware"         # P5 预留：立项前不出现在任何命令面（[ARCH-§11]）


@dataclass(frozen=True)
class Authorization:
    """由 SafetyGate.authorize() 签发的一次性凭据；后端只消费，不构造。
    后端执行前必须调用 gate.verify(auth, action) 复核（防误用/过期）。"""
    action: SafetyAction
    token: str                    # 随机一次性令牌（FR-SEC3）
    trace_id: str
    issued_at: float
    operator_present: bool        # 在场声明（声明式，进证据，FR-SEC5）


@dataclass(frozen=True)
class FlashRequest:
    artifact: Path
    target: str = "Easy521-0808TN"
    verify_after: bool = True     # 下载后程序校验，默认强制 [SPEC-FR-F3]
    source_project: Path | None = None   # 预检目标匹配用 [SPEC-FR-F4]


class FlashBackend(Protocol):
    name: str

    def check_environment(self) -> EnvironmentReport: ...
    def plan(self, request: FlashRequest) -> str:
        """零副作用预检：返回计划文本 + 确认短语（DOWNLOAD-<sha8>）。
        AI 两步协议的第一步（FR-SEC5）。"""
    def flash(self, request: FlashRequest, *, auth: Authorization,
              sink: ProgressSink) -> FlashResult:
        """auth.action 必须 == FLASH，且 token 未用过。"""
    def run(self, *, auth: Authorization, sink: ProgressSink) -> FlashResult: ...
    def stop(self, *, auth: Authorization, sink: ProgressSink) -> FlashResult: ...
```

### 6.4 MonitorBackend / MonitorSession（硬编码只读）

```python
@dataclass(frozen=True)
class MonitorTarget:
    transport: str                # "tcp" | "serial" | "virtual"
    host: str | None = None       # tcp：真机 IP 或 127.0.0.1
    port: int = 502               # 手册：从站默认 502 [01-A3]；虚拟从站 1502
    device: str | None = None     # serial：/dev/ttyUSB0（RS485 适配器，非编程 USB [RTU]）
    baud: int = 9600; parity: str = "N"; stopbits: int = 1   # 8N1 默认，实测为准
    unit_id: int = 1              # 从站不校验站号 [01-A3]
    word_order: str | None = None # None → 首连必须 calibrate [01-A4]


@dataclass(frozen=True)
class Sample:
    ts: float
    registers: tuple[int, ...]            # 原始字
    values: dict[str, float]              # 量名 → 解码值（契约 9 量 [00-四]）
    word_order: str
    flags: dict[str, str] = field(default_factory=dict)  # "range_Vpu": "ok" 等


class WordOrderReport(StrEnum):
    ABCD = "abcd"; BADC = "badc"; INDETERMINATE = "indeterminate"  # 如实报并列 [FR-M3]


class MonitorSession(Protocol):
    """只读会话：本 Protocol 刻意不含任何写方法 —— 类型级安全边界（[ARCH-§8.3] 第②层）。"""
    def calibrate(self) -> WordOrderReport: ...
    def anti_fake_check(self) -> bool:
        """签名寄存器双读 + 量程合理性（模拟器 D523 magic=521 [01-G]；
        疑 TUN 假连接 → False，CLI 拒绝除非 --accept-unverified-transport [03-①④]）。"""
    def snapshot(self) -> Sample: ...
    def stream(self, interval_s: float = 1.0, *,
               jsonl_path: Path | None = None) -> Iterator[Sample]: ...
    def close(self) -> None: ...


class MonitorBackend(Protocol):
    name: str                                     # "monitor_modbus" | "sim_virtual" | "native_usb"

    def check_environment(self) -> EnvironmentReport: ...
    def open(self, target: MonitorTarget) -> MonitorSession: ...
```

> pymodbus 3.16 实现注意：站号走 `device_id=`（`slave=` 已更名）[03-②]；单帧上限 FC03 读 125 字，契约 18 字无分帧问题 [02-1.2]。

### 6.5 工程脚手架与同步接口（v0.2 新增，承载 FR-L1/L2）

```python
# init/new 为 cli.py 的纯本地文本操作（模板随 plc_idf 包分发），不经过后端接口。
# sync 触碰 Windows 侧工程文件，必须是 bridge_autoshop 的显式能力：


@dataclass(frozen=True)
class SyncRequest:
    project_dir: Path             # ST 正文 + 变量 CSV 源目录
    autoshop_project: Path        # Windows 侧 AutoShop 工程目录
    dry_run: bool = True          # 默认只出 diff 预览（零 Windows 副作用）


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    diff_summary: list[Diagnostic]   # 改了哪些变量/正文段落（source="sync-diff"）
    evidence_id: str                 # 实际同步属 Windows 副作用，强制证据 [FR-E1]


class ProjectSync(Protocol):
    """bridge_autoshop 的附加能力：导入表驱动的 ST/CSV → AutoShop 工程同步。
    只搬运工程数据，不产生编译语义（[SPEC-FR-L4]）。"""
    def sync(self, request: SyncRequest, *, sink: ProgressSink) -> SyncResult: ...
```

### 6.6 后端注册表（`backends/__init__.py`）

```python
REGISTRY: dict[str, BackendEntry] = {
    # name             类                       层                    状态
    "bridge_autoshop": BackendEntry(BridgeAutoshopBackend, {"build", "flash", "sync"}, "active"),
    "monitor_modbus":  BackendEntry(ModbusMonitorBackend,  {"monitor"},            "active"),
    "sim_virtual":     BackendEntry(SimVirtualBackend,     {"build", "monitor"},   "active"),
    "native_usb":      BackendEntry(None,                  {"build", "flash", "monitor"}, "reserved"),
}

def get_backend(name: str, layer: str) -> BuildBackend | FlashBackend | MonitorBackend: ...
def backends_doctor() -> list[EnvironmentReport]: ...   # plcidf backends doctor [FR-X3]
```

规则：新后端 = 新文件实现对应 Protocol + `REGISTRY` 一行，`cli.py` 零改动 [SPEC-FR-X2]。`reserved` 条目必须在 `doctor` 中如实报"未实现" [FR-M8]。

### 6.7 退出码（NFR-REL2，全命令一致）

| 码 | 含义 | 典型场景 |
|---|---|---|
| 0 | 成功 | — |
| 2 | 用法错误 | 参数缺失/非法 |
| 3 | 环境不满足 | doctor 不通过、后端依赖缺失 |
| 4 | **授权拒绝** | 门禁三要素缺一（FR-SEC1） |
| 5 | 后端执行失败 | 编译错误、下载校验失败、监测中断、静态检查不过 |
| 6 | 设备冲突 | 租约被占、状态机拒绝转移 |

---

## 7. 关键数据流：sync → build → flash → monitor 时序

```mermaid
sequenceDiagram
    autonumber
    actor U as 用户(人类/AI)
    participant CLI as cli.py
    participant SG as safety.SafetyGate
    participant DM as device.DeviceManager
    participant BB as bridge_autoshop
    participant AS as AutoShop(Windows GUI)
    participant MB as monitor_modbus
    participant PLC as Easy521(502)

    rect rgb(240,246,240)
    note over U,PLC: 阶段零 sync（ST/CSV 变更进工程；--dry-run 零副作用 [SPEC-FR-L2]）
    U->>CLI: plcidf sync --dry-run
    CLI->>BB: sync(SyncRequest{dry_run=true})
    BB-->>CLI: SyncResult(diff_summary)
    CLI-->>U: diff 预览（AI 审阅/人确认）
    U->>CLI: plcidf sync（实际执行，Windows 副作用 → 证据）
    CLI->>BB: sync(SyncRequest{dry_run=false})
    BB->>AS: 导入表写入工程（UTF-16 [SPEC §2.3]）
    BB-->>CLI: SyncResult + evidence_id
    end

    rect rgb(238,242,247)
    note over U,PLC: 阶段一 build（无授权：编译是 Windows 副作用，留证据）
    U->>CLI: plcidf build --project plc/easy521 --json
    CLI->>DM: acquire(WINDOWS_AUTOSHOP)
    DM->>DM: 状态→bridge_busy（必要时自动 detach，记证据 [00-K5③]）
    CLI->>BB: build(BuildRequest, sink)
    BB->>AS: 打开工程→编译（autoshop_ui.ps1: file/click/accept/log [01-E2]）
    AS-->>BB: 编译输出 / 截图(owner-drawn 退回) / 产物
    BB-->>CLI: BuildResult(artifact, sha256, evidence_id)
    CLI->>DM: release → windows_free
    CLI-->>U: exit 0 + JSON（或 exit 5 + 诊断列表）
    end

    rect rgb(247,240,238)
    note over U,PLC: 阶段二 flash（门禁三要素，默认拒绝 [00-六]）
    U->>CLI: plcidf flash --artifact x --plan
    CLI->>SG: plan() → 预检报告 + 短语 "DOWNLOAD-<sha8>"
    CLI-->>U: 计划文本（零副作用）
    U->>U: 人类在对话中批准（AI 两步协议 [FR-SEC5]）
    U->>CLI: flash --allow-flash --operator-present --confirm "DOWNLOAD-<sha8>"
    CLI->>SG: authorize(FLASH) → Authorization(一次性令牌)
    CLI->>DM: acquire(WINDOWS_AUTOSHOP)
    CLI->>BB: flash(request, auth, sink)
    BB->>SG: verify(auth) 令牌复核
    BB->>AS: 下载 → 程序校验（对齐实证流程 [01-C]）
    AS-->>BB: 校验结果 / 反馈文本+截图
    BB-->>CLI: FlashResult(ok, verified, plc_state_source)
    CLI->>SG: 证据落盘（哈希/在场声明/结果）
    CLI->>DM: release
    end

    rect rgb(238,246,238)
    note over U,PLC: 阶段三 monitor（零授权：硬编码只读；不走设备管理器 USB 通道）
    U->>CLI: plcidf monitor --host <IP> --port 502 --calibrate --jsonl mon.jsonl
    CLI->>MB: open(MonitorTarget)
    MB->>PLC: FC03 D0-D17（首连双读 magic/量程防假连接 [03-①④]）
    PLC-->>MB: 18 字原始寄存器
    MB->>MB: ABCD/BADC 双解打分 → 字序判别 [01-A4]
    loop 每 interval_s
        MB->>PLC: FC03 (+FC01/02 可选, 全只读 [01-A3])
        MB-->>CLI: Sample（值 + 量程标记）
        CLI-->>U: 表格行 + JSONL 追加
    end
    end
```

四条目标不变量：(1) 授权在 safety.py 统一签发，后端消费凭据（P5 原则）；(2) flash/run/stop 全程持租约，monitor 与 sync 的 dry-run 不占用 USB；(3) 每阶段产出证据或结构化结果（P7）；(4) **源文本、厂商编译产物与设备部署结果分别记录并校验**，静态检查不能代替编译（[ARCH-§3.2]）。这些机制尚须按任务逐项落地。

---

## 8. 安全机制设计

### 8.1 威胁模型

| 威胁 | 场景 | 对策 |
|---|---|---|
| T1 误写真机 | AI/人误触发写、RUN、下载 | 门禁默认拒绝（[ARCH-§8.2]）+ monitor 类型面无写（§8.3） |
| T2 确认后换包 | 授权短语与产物脱钩 | 短语派生自产物 SHA-256（FR-SEC2） |
| T3 假连接假数据 | TUN fake-accept 劫持 [03-①] | 签名双读 + 量程校验 + 显式放行（FR-M5） |
| T4 并发抢占 | flash 进行中另一会话 attach 设备/AutoShop 被抢 | 设备租约互斥（[ARCH-§9]） |
| T5 抵赖/无法追溯 | 事后说不清谁在何时改了 PLC | append-only 证据 + 哈希链 + 截图（FR-E1~E4） |
| T6 陈旧锁死锁 | 持锁进程崩溃 | 租约含 PID/boot id，陈旧检测 + 显式清理留痕（FR-D3） |
| T7 固件级误操作（变砖） | P5 研究轨误触固件升级/调试口写 | FIRMWARE 动作单独授权档、立项前命令面不存在、变砖风险前置声明（FR-SEC7，[ARCH-§11]） |

### 8.2 授权门禁（`safety.py`）

策略表（单一事实源，CLI 与后端都不得绕过）：

| SafetyAction | 触发命令 | 显式旗标 | 在场声明 | 确认短语 | 证据 | 默认 |
|---|---|---|---|---|---|---|
| READ | monitor | 不需要 | 不需要 | 不需要 | 仅 JSONL 数据流 | **允许** |
| ATTACH / DETACH | device | 不需要 | 不需要 | 不需要 | 强制记录 | 允许（不触碰 PLC 状态，但改变 Windows/AutoShop 可用性 [00-K5③]） |
| WRITE | （预留，当前无后端使用） | `--allow-write` | 需要 | 需要 | 强制 | **拒绝** |
| FLASH | flash | `--allow-flash` | 需要 | `DOWNLOAD-<sha8>` | 强制 | **拒绝** |
| RUN | run | `--allow-run` | 需要 | `RUN-<sha8>` | 强制 | **拒绝** |
| STOP | stop | `--allow-stop` | 需要 | `STOP-<plan8>` | 强制 | **拒绝** |
| FIRMWARE | （P5 预留，立项前命令面不存在） | 单独授权协议（**不复用** flash/run 旗标） | 需要 | 需要 | 强制 | **拒绝**（[ARCH-§11] P5 红线） |

`<sha8>` = 操作对象（产物文件 / 计划文档 canonical JSON）的 SHA-256 前 8 位，由 `--plan` 打印。短语逐字比对。

```python
# src/plc_idf/safety.py 关键签名
class SafetyGate:
    def __init__(self, evidence_dir: Path, *, interactive: bool) -> None: ...
    def plan(self, action: SafetyAction, subject: dict[str, str]) -> str:
        """零副作用：打印计划 + 确认短语（两步协议第一步）。"""
    def authorize(self, action: SafetyAction, *, flag_given: bool,
                  operator_present: bool, confirm_phrase: str | None,
                  subject: dict[str, str]) -> Authorization:
        """三要素齐备才签发；任一缺失 → AuthorizationDenied（exit 4）。
        签发即写证据（attempt 条目，含成功/拒绝）。"""
    def verify(self, auth: Authorization, action: SafetyAction) -> None:
        """后端执行前复核：令牌未用、动作匹配、未过期；用后即焚。"""
```

设计决策说明：
- **门禁在 CLI 层统一执行，后端不自查旗标**——防止某后端"忘了检查"；后端只认 `Authorization` 凭据（P5 原则）。
- **授权不可持久化**：无 `--yes`、无配置文件记忆、无环境变量开关（FR-SEC3）。每次操作重新走三要素，代价是一次复制粘贴，收益是不可绕过。
- **`--operator-present` 是声明式控制**：技术上无法验证人类在场，因此 (a) 进证据供事后审计，(b) SPEC A2 假设将其定为组织约定——AI 只可在人类于本会话明确批准该操作后置位（FR-SEC5）。这是诚实的设计：不假装技术控制能覆盖组织问题。
- **FIRMWARE 独立成档**：固件级实验风险等级高于任何用户工程操作（变砖），不复用任何既有授权旗标，且 P5 立项前该动作在代码中不存在（FR-SEC7）。

### 8.3 monitor 只读的四层防线（纵深防御）

| 层 | 机制 | 失效假设 |
|---|---|---|
| ① CLI 面 | `monitor` 子命令不存在任何写相关参数（对照：flash/run/stop 全在 monitor 之外） | AI/人无法从命令面表达写意图 |
| ② 类型面 | `MonitorSession` Protocol 无写方法；实现类不得新增 public 写方法（review + 静态检查） | 调用方拿到会话也无处可写 |
| ③ 实现面 | 后端源码仅 import 读调用（`read_holding_registers/read_coils/read_discrete_inputs/read_input_registers`），对齐 wsl-tools 既有硬边界 [wsl-tools/README §2] | 即使改代码，diff 一目了然 |
| ④ 对端面 | 虚拟从站对写功能码回异常（既有行为 [00-四]）；真机侧由"上位机只读"组织约束兜底 | 纵深兜底 |

测试锁死：静态扫描测试（③）+ 端到端写拒绝测试（④）纳入回归（AC-2）。

### 8.4 证据机制（`safety.py` 内实现数据访问）

- 位置：`evidence/<UTC日期>/<trace_id>/`——`events.jsonl`（append-only）+ 截图/日志文件；schema 带 `schema_version`（FR-E4）。
- 条目字段：`ts, trace_id, actor("human"|"ai:<session>"), action, backend, subject_sha256, operator_present, result, refs[]`。
- `evidence verify`：重算条目链哈希 + 产物哈希存在性核对（T5 对策）。
- 编译/下载/同步关键步骤的截图由 bridge_autoshop 经 ProgressSink `EVIDENCE` 事件推送落盘（[01-E2] 能力边界内）。

---

## 9. 设备互斥状态机（`device.py`）

USB 编程口同时只能有一个主人：WSL（attach 后 `/dev/ttyACM0`）或 Windows（AutoShop 经 libusb0 驱动 bulk 端点 [00-K2]）。这是实测确认的互斥约束 [00-K5③]，因此**所有权切换只能经设备管理器**，任何后端不得私自调用 usbipd。

```mermaid
stateDiagram-v2
    [*] --> WINDOWS_FREE : usbipd bind 完成（初始）

    WINDOWS_FREE --> WSL_ATTACHED : attach()（证据记录）
    WSL_ATTACHED --> WINDOWS_FREE : detach()（证据记录）

    WINDOWS_FREE --> BRIDGE_BUSY : acquire(WINDOWS_AUTOSHOP) 桥式 build/flash/run/stop/sync
    BRIDGE_BUSY --> WINDOWS_FREE : release()（租约释放）

    WSL_ATTACHED --> WSL_ATTACHED : acquire(WSL_NATIVE)（预留：native_usb 侦察/监测）

    BRIDGE_BUSY --> STALE : 持锁进程死亡 / 租约超时
    WSL_ATTACHED --> STALE : 同上
    STALE --> WINDOWS_FREE : stale_clean(--force)（证据记录）
```

| # | 当前态 | 事件 | 动作 | 次态 | 拒绝条件（exit 6） |
|---|---|---|---|---|---|
| T1 | WINDOWS_FREE | attach | usbipd attach --wsl；建档 | WSL_ATTACHED | — |
| T2 | WSL_ATTACHED | detach | usbipd detach；归还 | WINDOWS_FREE | — |
| T3 | WINDOWS_FREE | acquire(WINDOWS_AUTOSHOP) | 落租约锁 | BRIDGE_BUSY | 已有租约（T4/T6 兜底） |
| T4 | BRIDGE_BUSY | acquire(任意) | — | 不变 | 租约被占 → 提示持有者（T4） |
| T5 | BRIDGE_BUSY | attach / detach | — | 不变 | **桥式会话期间禁止切换所有权** |
| T6 | 任意 | acquire 时陈旧检测 | 报 STALE | STALE | 需 `stale-clean --force` 才能继续 |
| T7 | NETWORK（网口模式） | 任意 | 不涉 USB，仅记录 | 不变 | — |

租约锁协议：`<repo>/.plcidf/device.lease.json` = `{state, purpose, pid, boot_id, ts, holder}`；陈旧判定 = PID 不存活或 boot_id 变化或超 TTL；`stale-clean` 清理并写证据（T6）。`device status` 读取即校验，不做隐式清理。

设计决策：**attach/detach 不需要授权旗标但强制证据**（[ARCH-§8.2] 策略表）——它不改变 PLC 应用状态，但会中断 AutoShop 可用性，属于"低风险高干扰"操作，用可见性而非闸门管理。桥式会话内的 T5 拒绝则保护"下载中途 USB 被抢走"这一真实事故面。

---

## 10. 与 ESP-IDF 的对照（命名对标）

项目名 **PLC-IDF** 表达 ESP-IDF 风格的终端工作流目标。对标范围还包括项目配置、版本依赖、编译诊断、产物追溯、下载验证和 CI，不能仅按命令名判断完成度。下表区分目标能力，当前缺口见评审矩阵：

| 维度 | ESP-IDF | PLC-IDF | 说明 |
|---|---|---|---|
| CLI | `idf.py build / flash / monitor` | `plcidf build / flash / monitor` | **同构动词**；包名 `plc_idf` ↔ `esp-idf` 同级 |
| 源语言 | C / C++（全文本） | **IEC 61131-3 ST**（全文本，[ARCH-§3]） | 文本化对文本化；LD/FBD 图形语言被排除（SPEC §2.1） |
| 编译执行体 | 目标相关编译工具链 | 首期验证 **bridge_autoshop**（Windows 厂商编译） | 本目标原生编译后端尚未验证，不能从桥接调用成功推出产物有效 |
| 烧录 | esptool（开源 bootloader 协议） | FlashBackend 插槽：现桥式，未来 `native_usb` | esptool 协议公开；Easy521 下载协议未逆向 [02-1.4] → 同插槽换实现 |
| monitor | idf_monitor（设备日志等反馈） | monitor_modbus（Modbus TCP 寄存器数采）| 均可提供反馈，但日志、变量、运行状态与调试能力不能视为等价 [ARCH-§4] |
| 串联 | `idf.py build flash monitor` 一键 | 分步 + flash 单独授权确认 | **有意取舍**：授权门禁（P5 原则）优先于一键便利 |
| menuconfig | 有（Kconfig） | 无 | 工程配置即文件（CSV 契约/导入表），无需菜单系统 |
| JTAG/OpenOCD | 有 | —（无对应） | Easy521 无公开调试口协议；底层探索归 P5 研究轨（[ARCH-§11]） |
| 后端结构 | 面向支持目标的工具链与烧录/监控工具 | 目标设计为多后端；注册机制待实现 | PLC-IDF 首期桥接厂商工具，后续替换须由兼容性和运维成本验收支持（P1/P2） |

---

## 11. 演进路线（后端替换矩阵 + 研究轨）

对齐 00 总纲路线图 [00-五]，表达为"同一插槽、更换实现"；P5 为与主线**正交**的研究轨：

| 阶段 | 触发条件 | BuildBackend | FlashBackend | MonitorBackend | 用户命令面 |
|---|---|---|---|---|---|
| **P1 软件演示与工程基线**（数据演示已具备，工程检查待实现） | — | 静态检查待实现；sim_virtual 不编译 ST | （无，默认拒绝） | sim_virtual（固定场景虚拟从站） | 现有五命令；后续按需求扩展 |
| **P2 桥式全链**（进行中，人在场） | USB 单线即可 [00-五P2] | **bridge_autoshop**（+sync） | **bridge_autoshop** | monitor_modbus（虚拟→真机待网口） | 不变 |
| **P3 抓包评估**（待立项 [00-五P3]） | USBPcap 抓 AutoShop 会话 → 帧结构分析；产出进 `docs/research/` | bridge_autoshop | bridge_autoshop（若帧结构可逆向 → 启动 native_usb 原型） | monitor_modbus + native_usb 原型 | 不变 |
| **P4 网口直连/原生**（待拓展坞 [00-五P4]） | 网线 + IP 发现 + 字序真机标定 | bridge_autoshop | bridge_autoshop（网口下载协议同样未逆向，仍桥式） | **monitor_modbus 升主位**（真机 502，零 Windows 依赖） | 不变 |
| **P5 底层/固件研究轨**（离线研究进行中；真机实验未授权） | 本次研究需求；真机操作另行逐项授权 | 不改变现有编译能力 | 不新增固件写操作 | 不新增调试口操作 | 当前无固件命令面 |
| **演进候选** | 分层验收 | 厂商 GUI 桥接、Windows DLL 无头调用或独立 Linux 原生后端分别评估 | native_usb（若 P3 验证成功）/ 否则桥式 | monitor_modbus / 经验证的 USB 后端 | 核心动词稳定，能力状态如实报告 |

**P5 研究轨内容**：(a) 厂商固件包与升级机制的离线资料研究，已有会话样本的离线分析；采集升级过程需要触发真实升级时，另作写操作审批，不能称为零风险；(b) USB 自述 `Linux 3.10.65 with sunxi_usb_udc` 的来源与原始证据复核，以及 FEL、UART 等通用公开资料调研，不据此确认具体 SoC 或恢复能力；(c) ARM ELF 应用产物、ABI 与运行时依赖分析。证据和未知项见 [底层复核](../research/firmware/03-feasibility-assessment-2026-10-17.md)，产出进 `docs/research/`。

**P5 安全红线（前置声明）**：
1. **一切固件级实验默认禁止接触真机**——文档研究、抓包离线分析不受限，任何发往真机的固件/调试口操作受限；
2. 须用户**单独授权**（SafetyAction.FIRMWARE 独立档，不复用 flash/run 授权，[ARCH-§8.2]），且逐次实验逐一授权，不存在"研究期总授权"；
3. **变砖风险前置声明**：固件操作可能使 PLC 不可启动且无法自行恢复；每次实验前须向用户复述此风险并留声明证据（FR-SEC7）。

演进判据：当前应先验证“原生监测 + 厂商桥接编译/下载”的交付路径；若原生后端长期不能满足兼容性与维护成本要求，桥式可以保留。现有公开资料和本机 ARM ELF 中间产物均不足以证明 Windows 永远不可替代，也不足以证明可直接生成可部署程序。原生编译、程序下载协议、固件/运行时替换分别立项并分别验收；固件研究不作为主线交付的前置依赖。

---

## 12. 风险与对策

| # | 风险 | 等级 | 对策 | 依据 |
|---|---|---|---|---|
| R1 | GUI 驱动的桥式编译脆弱（弹窗/焦点/owner-drawn 输出读不出） | 高 | accept/log/screenshot 组合策略；owner-drawn 一律退回截图 [01-E2]；失败恢复 GUI 可用状态（NFR-REL4）；证据留痕便于复盘 | 01-E2 |
| R2 | 软件 RUN 失败根因未明（拨码假设待证） | 高 | run 结果只记录不臆断（FR-F5/F7）；首次真机联调先核面板拨码与灯 [01-D]；AC-6/AC-12 挂验收 | 01-D, 00-K6 |
| R3 | REAL32 字序真机与契约不符 | 中 | 运行时 `--calibrate` 强制判别（FR-M3）；BADC 全链备选；判别不了如实报（fail-closed） | 01-A4, 00-K3 |
| R4 | TUN 假连接污染监测数据 | 中 | 防假三校验（FR-M5）+ 显式放行旗标；联调前置 TUN 检查提示（FR-SEC6） | 03-①, 00-K5① |
| R5 | WSL→Windows 不通（Hyper-V 防火墙）阻断未来"Windows 模拟器桥" | 中 | 当前选择 WSL 内虚拟从站（零配置 [03-④]）；若需连 h5u_daemon 再加放行规则 | 03-①, 01-G |
| R6 | 设备互斥被后端绕过 | 中 | usbipd 调用收敛在 device.py 单点；状态机转移表测试（AC-7） | 00-K5③ |
| R7 | usbipd 无法从 WSL 触发（策略） | 低 | 降级为打印手工命令（A4 对策）；bridge 流程不阻塞 | 03-③ |
| R8 | pymodbus 3.x API 漂移 | 低 | 版本钉 `>=3.16,<4` [02-2.7]；踩坑记录（`device_id=`）写进实现注释与测试 | 03-② |
| R9 | 证据目录膨胀（截图二进制） | 低 | 截图按 trace 归档 + 清理策略开放问题（§13-O4） | — |
| R10 | AI 过度自治越过门禁 | 中 | 门禁在代码层默认拒绝（不是提示）；两步协议 + 证据审计（FR-SEC5）；SPEC A2 组织约定兜底 | 00-六 |
| R11 | AutoShop 导入表格式随版本漂移，sync 写坏工程 | 中 | 格式锚定 4.11 实证 [SPEC §2.3]；sync 默认 dry-run + 同步前工程文件备份进证据；版本变更回归（A6 对策） | §2.3, C13 |
| R12 | P5 固件实验变砖 | 高 | 红线三件套：默认禁止接触真机 + 单独授权 + 风险前置声明（FR-SEC7，[ARCH-§11]）；离线分析不受限保证研究价值 | 01-B, 00-六 |

---

## 13. 留给实现侧的开放问题

| # | 问题 | 建议 |
|---|---|---|
| O1 | 桥式会话异常中断后 AutoShop 的状态恢复策略（杀进程？弹窗遍历 accept？） | 先实现"超时 → 截图取证 → 释放租约 → 人工介入"的保守路径 |
| O2 | 计划文档（确认短语派生源）的 canonical 序列化需 cli/safety 共用同一实现 | 放 safety.py，JSON `sort_keys+separators` 固定 |
| O3 | `sim_virtual` 是否保留 BuildBackend 角色（静态检查），还是缩为纯 monitor 后端 + `build --check` | 倾向后者，语义更诚实（FR-B6/L4 的"非厂商编译"标注仍保留） |
| O4 | 证据目录位置（repo 内 `evidence/` vs `~/.plcidf/`）与 `.gitignore`、容量清理策略 | repo 内 + `evidence/` 已有先例（plc/easy521/evidence），补容量上限与清理命令 |
| O5 | RUN 状态能否经只读通道读回（系统变量/监控文本），避免依赖人读指示灯 | P4 真机首连时验证（与 AC-12 合并） |
| O6 | `native_usb` 是否复用 `MonitorBackend` 还是新增 raw 帧插槽 | P3 帧结构明确后再定，接口层不预设（[ARCH-§11]） |
| O7 | TUN 开启的可自动检测（Windows 路由 198.18.0.0/16 [03-①]）是否做成 doctor 检查项 | 低成本高收益，建议纳入 `backends doctor` |
| O8 | 多 WSL 发行版并发场景的租约语义 | 本期按"单机单发行版"假设，锁文件机器域即可 |
| O9 | **Easy521-0808TN 的 RS485 实物接线与组态参数**：手册已记录本体 1 路及端子定义（[ARCH-§4]），本机尚未通信验收 | 独立记录站号、串口参数与只读通信证据，软件支持不等同于本机验收 |
| O10 | `sync` 的 AutoShop 侧落地方式：文件对话框导入（autoshop_ui.ps1 `file` 动作）vs 直接写工程文件再打开 | 先走 GUI 导入对话框（实证路径），直接写文件留待 P3/autoshop-mcp 线索评估；无论哪种，同步前工程目录快照进证据 |
| O11 | init/new 脚手架模板的存放与版本（包内 data vs 仓库 `templates/`） | 模板随包分发（`importlib.resources`），版本随包；仓库只留生成示例 |
