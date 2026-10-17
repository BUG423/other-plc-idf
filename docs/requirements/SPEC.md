# PLC-IDF 需求规格说明书（SPEC）

> **后续实装更新（2026-10-17）**：现已增加 `new/init/check/doctor` 及已有工程 MAIN 的 `sync`、`build --source`；Windows 4.12 正负编译与独立 wheel 通过实测，见 [现场验证](../management/LIVE_VALIDATION_2026-10-17.md)。下文“五命令”等边界描述为首轮审计快照。完整变量/任务同步、真机网口、下载/运行及全部目标 API 仍未完成，不能将本草案视为当前功能清单。

| 项 | 值 |
|---|---|
| 文档编号 | PLC-IDF-SPEC-001（原 py2plc-SPEC-001，随 v0.2 更名） |
| 版本 / 状态 | v0.2 · 设计评审稿（2026-10-16；2026-10-17 勘误） |
| v0.2 变更 | ① 更名 PLC-IDF（包 `plc_idf`，CLI `plcidf`，`py2plc` 兼容别名）；② 新增 §2 源语言层（IEC 61131-3 ST）与 FR-L 需求段；③ 新增 §3 设备开放监控接口事实节；④ 约束补 C12 许可证；⑤（架构侧）演进矩阵新增 P5 研究轨，见 [ARCH-§11] |
| 关联文档 | [架构设计](../architecture/ARCHITECTURE.md)（接口与机制的设计落点）· [00 总纲](../wsl-feasibility/00-synthesis-and-roadmap.md)（可行性结论与红线） |
| 事实依据 | [01 协议硬事实](../wsl-feasibility/01-protocol-facts.md) · [02 开源生态](../wsl-feasibility/02-opensource-ecosystem.md) · [03 WSL 实测](../wsl-feasibility/03-wsl-probe-results.md) · `wsl-probe/usb-rtu/` |
| 读者 | 实现侧（`src/plc_idf/` 编码者）、评审者、AI Agent（本文即其行为规范之一） |

> **2026-10-17 勘误与实现边界**：本文描述目标需求，不是已交付功能清单。当前 CLI 只有 `build / flash / monitor / sim / devices`；`init/new/sync`、`--backend`、全命令 `--json`、`--plan`、哈希绑定授权、设备租约、`evidence` 和 `backends doctor` 等仍是待实现设计。`sim` 是固定场景的 Modbus 数据演示，不执行用户 ST；`build/flash` 的 GUI 编排不等于已完成厂商编译及程序校验验收。当日 strict bool、GUI 动作不重试及未校验不报成功三项修复已落地并通过软件集成验证，结果见 [进展记录](../management/PROGRESS.md)；这些修复不代表真实编译、下载校验或真机全链闭环已实现。
>
> 本次同时纠正“ST 是唯一可代码化语言”“没有开源实现即不可能原生编译”“有限 USB RTU 探测证明所有配置均不支持”等过度推断。需求—实现—验收矩阵与优先级见 [架构评审](../reviews/ARCHITECTURE_REVIEW_2026-10-17.md)；ARMv7-A/ELF 产物线索及固件研究边界见 [底层可行性复核](../research/firmware/03-feasibility-assessment-2026-10-17.md)。历史侦察档案保留原貌，当前结论以有证据的复核为准。

> **命名与兼容**：产品名 **PLC-IDF**，Python 包 **`plc_idf`**，CLI 命令 **`plcidf`**；`py2plc` 保留为兼容别名（仓库物理目录名亦暂保留）。本文行文统一用 `plcidf` 指代命令。
>
> **引用约定**（全文通用）：
> `[00-K#]` = 00 总纲「三、关键事实」第 # 条；`[01-X#]` = 01 文档 X.# 节；`[02-#.#]` = 02 文档 §#.#；`[03-①]` = 03 文档 §①；`[RTU]` = `wsl-probe/usb-rtu/` 原始探测数据；`[ARCH-§]` = 架构文档章节。本文所有技术断言均须可追溯到以上出处；无法追溯的写成"假设"（见 §8.2）。

---

## 1. 背景与目标

### 1.1 背景

项目目标是把 **汇川 Easy521-0808TN** PLC 的开发体验做成 **ESP-IDF 风格**：在 WSL（Linux）里通过 `plcidf build / flash / monitor` 等命令完成编译、下载、监测，让 AI 获得可验证反馈。项目名 **PLC-IDF** 表达这一定位；命令名对齐不代表功能等价，还需完成文本工程同步、厂商产物识别、下载校验与运行验收（[ARCH-§10]）。

三条能力链的当前证据与待验证项如下；历史侦察结论见 [00-二]，检索未发现不等于技术上不存在：

| 能力链 | 纯 WSL 原生 | 结论 | 依据 |
|---|---|---|---|
| 监测（网口 Modbus TCP） | ✅ 可行 5/5 | 502 出厂默认开启，D0-D7999 ↔ FC03 地址直映，协议完全公开 | [01-A3] [02-1.2] 三方互证 |
| 编译（代码 → 工程产物） | 尚未验证 | 已有 AutoShop Windows 工具链；历史调研未找到可直接用于本目标的公开原生 LiteST 编译器或受支持无头接口；不排除后续适配或原生研究 | [02-2.4] [01-E]；2026-10-17 底层复核 |
| 下载（产物到 PLC） | 尚未验证 | 项目尚未掌握可复用的 USB/以太网程序下载协议实现，当前选用厂商桥接；不能从检索结果推出原生实现不可能 | [02-1.4] [02-1.5] [01-B] |

历史档案记录 USB 透传及 `/dev/ttyACM0` 可打开；CDC/bulk 描述符的原始输出尚未归档，证据分级见底层复核。15 组 RTU 参数探测均无响应 [RTU]，不足以建立可用监测通道，也不能穷尽证明所有配置均不支持 RTU；当前不把编程 USB 口作为受支持的 RTU 通道，编程协议仍待验证 [00-K2]。

### 1.2 第一性原理：用户要什么

**核心需求是在 WSL/终端中获得可验证的开发闭环。** 是否接受 Windows 后端，以及哪些能力必须原生运行，应分别记录为部署约束；不能用体验对齐替代用户对去 Windows 化的研究诉求。首期拆为四个目标：

1. **单 CLI 入口**：`plcidf <verb>`，与 `idf.py <verb>` 同构，学习成本趋零（[ARCH-§10] 对照表）。
2. **减少人工 GUI 操作**：工程同步、编译和下载由后端驱动；GUI 桥接可能仍要求 Windows 交互桌面，其占用和错误截图须如实呈现。
3. **数据回流**：每次操作产出结构化结果与证据（产物哈希、截图、JSONL 监测流），AI 可读、可审计、可追溯。
4. **后端可替换**：`build/flash/monitor` 三个动词背后的执行体可插拔。今天用桥式（Windows 当不可见编译服务器），明天某层协议逆向成功就换成原生后端——**用户命令不变**。

当前已知的厂商编译工具在 Windows，项目尚未掌握原生下载协议 [02-2.4] [02-1.4]。它们是当前实现约束，不是用户需求；架构先验证桥式后端，再根据独立研究结果评估替换（[ARCH-§11]）。

### 1.3 目标

- **G1** 在 WSL 内提供 `plcidf` 单一 CLI，覆盖编写之后的完整闭环：**工程脚手架 → ST 编写 → 同步 → 编译 → 烧录 → 监测 → 仿真**。
- **G2** 桥式形态下实现"AI 直接上传编译代码 + 拿回反馈"的完整可达链路 [00-核心判断]。
- **G3** 安全设计为一等公民：对真机默认只读；一切写副作用走显式授权门禁并留证据（对齐 00 红线 [00-六]）。
- **G4** 后端接口稳定：新后端（原生 USB、以太网原生）接入不改用户命令面与上层代码。
- **G5** 现有资产零浪费：`wsl-tools/`（虚拟 PLC、监测、65 项测试）、`plc/tools/autoshop_ui.ps1`（GUI 驱动）与 `plc/easy521/` 的 **ST+CSV 工程资产及导入工作流**作为首批实现的地基被复用而非重写 [00-四]（[ARCH-§3]）。

### 1.4 非目标（本期明确不做）

- **N1** 逆向或复刻 AutoShop 私有下载/监控协议（P3 立项后才评估，[02-1.4] 工程量与变砖/合规风险不可控）。
- **N2** 无人监督的真机写操作。GUI 驱动脚本"不应作为无人监督的硬件部署脚本"是档案明言的定位 [01-E2]。
- **N3** 图形语言前端：首期不实现 LD/FBD 的图形编辑或图形↔文本转换；选用已有资产和导入路径较明确的 ST（[ARCH-§3]）。
- **N4** 自研 LiteST/ST 编译器：首期复用厂商编译语义；原生编译器需要单独验证方言、ABI、链接和运行时接口（§2.2、约束 C1）。
- **N5** 图形界面（含托盘、Web 面板）。TUI 之外的展示一律走文件与 JSON。
- **N6** 支持 Easy521 之外的 PLC 型号（H5U、Easy320 等仅作后端抽象的兼容性考虑，不做适配）。
- **N7** 把 20256/20257 端口作为既定事实——历史检索未证实其与本目标关联 [02-1.3]，须有本机证据才可采用。

---

## 2. 源语言层：IEC 61131-3 ST（前端语言）

### 2.1 选型论证

从项目候选语言与现有资产出发，比较文本审查和自动生成的实施成本；下表是首期选型，不是对所有标准版本或厂商能力的完整判定：

| 语言 | 形态 | 可 diff/review | AI 可生成 | 结论 |
|---|---|---|---|---|
| LD 梯形图 | 图形（继电器触点/线圈） | 需可解析序列化和语义比较工具；本项目未实现 | 可通过图模型或中间表示生成；本目标转换链未验证 | 首期不纳入 |
| FBD 功能块图 | 图形（信号连线） | 需明确块、端口与连接语义；本项目未实现 | 可生成；本目标导入链未验证 | 首期不纳入 |
| SFC 顺序功能图 | 图形 + 步/转移 | 需序列化及状态语义检查 | 可生成；本项目未验证 | 首期不纳入 |
| IL 指令表 | 文本（类汇编） | 可逐行比较 | 可生成；低层表示及目标支持需单独评估 | 不作首期前端 |
| **ST 结构化文本** | **文本（类 C/Pascal）** | ✅ 逐行 diff、PR review | ✅ 与现有算法资产（Python/ST 移植线 [00-四]）同构 | **选定** |

**结论：ST 是本项目首期选定的前端。** 它可直接逐行审查，与现有算法和 AutoShop 导入资产衔接，降低实现成本。IL 也是文本；图形语言也可序列化、生成和审查，只是本项目尚未验证 Easy521/AutoShop 的相应转换链。选择 ST 不依赖“其他语言无法由 AI 使用”这一断言。

### 2.2 事实约束（不得虚构的部分）

1. **当前缺少经过目标验证的原生编译后端**：历史检索未找到可直接复用的 Easy521/LiteST 开源编译器 [02-②]；这不证明它不存在，也不排除后续自研或适配。
2. **首期选用 AutoShop 厂商编译语义**：`bridge_autoshop` 是当前选定方案，未来 DLL 直调仍属于 Windows 执行。LiteST 与标准 ST 存在方言差异（实证：本工程所用 LiteST 不支持 `LREAL`，因此采用 32 位 REAL，见 `plc/easy521/README.md`）。
3. **当前 WSL 实现边界**：编辑、静态检查、数据仿真、监测、编排和证据；其中脚手架与静态检查仍待实现。不得把静态检查、Python 镜像或 GUI 按键执行称为已完成厂商编译。未来若实现原生 ST 编译，必须以 ABI、链接、厂商对照及设备运行验收证明兼容性；ARM ELF 产物线索本身不足以证明可部署。

### 2.3 工作流的资产实证

现有资产已验证"ST + 变量 CSV → AutoShop 工程"工作流 [01-E3]（`plc/easy521/`）：

- `MAIN_5ms_body.st`、`FB_BESS_AdaptiveSupervisor_Easy521_body.st`：LiteST 正文源（文本，可 diff）；
- `MAIN_5ms_variables.csv`、`FB_..._variables.csv`：变量表清单（契约的可读形态）；
- `autoshop_global_import.csv`、`autoshop_fb_import.csv`：**依据 AutoShop 真实模板生成并已成功导入**的导入表（注意：AutoShop 4.11 Unicode 导入使用 UTF-16，工作区为 UTF-8，须转码；不同版本须重新核对原生格式）；
- 寄存器契约 `modbus_register_map_real32.csv`：ST 变量 ↔ FC03 地址的映射唯一定义。

即：**前端语言的完整闭环（文本源 → 变量清单 → 厂商工程）已在真机工程上人工走通**，`plcidf init/new` 与 `plcidf sync`（FR-L1/L2）要做的只是把这条人工流程自动化，不引入任何新的编译语义。

### 2.4 语言层功能概要（FR-L，详见 §6.1）

| 命令 | 作用 | 编译语义 |
|---|---|---|
| `plcidf init` / `plcidf new <name>` | 生成 ST+CSV 工程脚手架（POU 骨架、变量表模板、寄存器契约引用） | 无（纯文本产物） |
| （编辑） | 人/AI 修改 `.st` 与 `*_variables.csv` | 无 |
| `plcidf sync` | 把 ST/CSV 变更经桥式同步进 AutoShop 工程（导入表驱动，diff 预览先行） | 无（仅工程数据操作，经 AutoShop GUI/导入表） |
| `plcidf build` | 规划桥式 AutoShop 编译（[ARCH-§6.2]）；产物验收待完成 | 首期选用 AutoShop |

---

## 3. 设备开放监控接口（事实节）

回答"板子提供什么监控接口"。Easy521-0808TN 的对外通道按开放程度分三档：

| 通道 | 物理层 | 协议 | 开放程度 | 定位 |
|---|---|---|---|---|
| **以太网 Modbus TCP（主通道）** | 2×RJ45，10/100M 自适应 [01-A1] | **502 端口出厂默认开启**，最多 31 客户端；FC01/02/03/04/05/06/15/16；**D0-D7999 ↔ FC03 地址 0-7999 一比一直映**；寄存器内大端；**主站按 IP 寻址、从站不校验站号** [01-A3] | 完全公开（标准协议 + 官方映射表，经 EMQX Neuron 与 HslCommunication 三方互证 [02-1.1][02-1.2]） | **监测主通道**：`monitor_modbus` 后端，P4 起为真机主位 |
| RS485 Modbus RTU | 本体 1 路，485+/485-/GND 端子，见 [手册复核](../research/firmware/01-manual-facts.md) | RTU 从站映射与以太网相同（编程手册 p386 [01-A3]）；通信参数需组态 | 协议公开；本机实物接线、站号和串口参数待验收 | 备选通道：`monitor_modbus` 串行传输已备；软件支持与真机验收分别报告 |
| USB 编程口 | Type-C；档案记录 CDC 拓扑及 bulk EP 0x02/0x81、512B [00-K2]，原始描述符待补 | 项目尚未掌握可复用的编程协议；15 组 RTU 探测无响应 [RTU]，当前不支持以 RTU 方式监测 | 未验证开放实现 | **P3 抓包评估对象**（[ARCH-§11]）；当前作为桥式通道的 Windows 侧资源 |

设计含义：(1) 公开 Modbus 监测与尚未掌握的编程协议分别适配；(2) 当前手册复核未确认 32 位浮点跨寄存器字序 [01-A4]，首连须建立校准证据（FR-M3）；(3) 固件升级/烧录属于 **P5 底层/固件研究轨**（[ARCH-§11]），当前开展离线研究，真机操作须单独授权。

---

## 4. 干系人与用户

### 4.1 用户画像

| 用户 | 描述 | 关键诉求 | 对应需求重心 |
|---|---|---|---|
| **U1 人类开发者** | 电力/嵌入式工程师，在 WSL 里写 ST 代码、跑联调 | 统一命令、结果可信、减少误操作并能审计恢复 | FR-L/B/F/M/D，NFR-SEC |
| **U2 AI Agent（一等用户）** | Claude 等 CLI 驱动的编码代理，负责 ST 编写、同步、编译、烧录、监测的全程闭环 | 机读输出、确定性退出码、无 TTY 也能走完授权协议、证据文件可直接引用 | FR-E、FR-SEC5、NFR-OBS、NFR-REL2 |

**U2 是一等用户，不是附加场景**：本项目的主要工作流就是 AI 闭环 [00-核心判断]。由此产生的硬性要求（详见 §6）：所有命令支持 `--json`；退出码语义稳定（[ARCH-§6.7]）；授权采用"两步确认协议"使无 TTY 的 AI 与有 TTY 的人走同一门禁（FR-SEC5）；文档与契约（寄存器表、变量 CSV、接口签名）在仓库内机器可读——**ST 前端的文本化正是这一要求在语言层的落点**（§2.1）。

### 4.2 外部系统与依赖

| 系统 | 角色 | 约束来源 |
|---|---|---|
| AutoShop V4.11.0.5（Windows 主机，后续版本需重新验证） | 首期选定的厂商编译、下载执行器与工程同步对象（GUI，由 `autoshop_ui.ps1` 驱动） | [01-E2]：当前脚本提供 inspect/screenshot/click/file/accept/log 等 UI 动作；本目标无头编译接口待验证 [02-2.4] |
| usbipd-win | USB 设备 attach/detach 的所有权切换 | [00-二]（usbipd 挂载打通、cdc_acm 认领 ttyACM0 实测）· [00-K5③]（与 AutoShop 互斥） |
| Easy521 真机 | Modbus TCP 从站（502）；USB CDC 编程口（§3） | [01-A3] · [01-B] · [RTU] |
| 虚拟 Easy521（`wsl-tools/virtual_easy521.py`） | 仿真闭环的从站，三场景 + JSONL | [00-四] |
| AutoShop 离线调试模拟器（`h5u_daemon.exe`，127.0.0.1:502） | 厂商语义参考（FC03 偏移=D 编号直映，D523 magic=521） | [01-G]；WSL2 NAT 下不可直达 [03-①] |
| Windows 防火墙 / 代理 TUN | 环境雷：WSL→Windows 入站默认丢弃；TUN 开启时 TCP 假连接 | [03-①] [00-K5①②] |

---

## 5. 用例模型

### 5.1 用例图（文字型）

```
                       ┌───────────────────────────────────────────────────────┐
                       │                    PLC-IDF 系统边界                     │
                       │                                                       │
   人类开发者(U1) ──────┼──► UC-01 编写 ──► UC-02 编译 ──► UC-03 烧录(授权) ──► UC-04 监测
        ▲              │       (init/new/  │              │               │
        │  审批/在场    │        sync/ST)   │      《include》授权门禁《include》授权门禁
        └──────────────┼───────────────────┼──────────────┴───────────────┘
                       │                   ▼                                     │
   AI Agent(U2) ───────┼──► UC-05 仿真闭环（虚拟从站，无需授权）                    │
        │              │                                                       │
        ├──────────────┼──► UC-06 设备管理 device（USB 所有权/互斥）              │
        ├──────────────┼──► UC-07 证据回溯 evidence（查询/校验）                  │
        │              │                                                       │
   后端开发者(U1) ──────┼──► UC-08 后端扩展（实现 protocol + 注册）                │
                       │                                                       │
   外部系统：AutoShop(Windows, 首期编译后端) · usbipd · Easy521(502/USB) · 虚拟Easy521 ─┘
```

《include》：UC-02、UC-03（及一切写副作用用例）必须经过授权门禁（FR-SEC1）。UC-04 按设计**不可能**发起写（FR-SEC4），故无授权环节。UC-01 产出的纯文本变更不触碰真机，无门禁。

### 5.2 用例清单

| 编号 | 名称 | 主角 | 触发 | 主流程（摘要） | 异常流 | 验收锚点 |
|---|---|---|---|---|---|---|
| UC-01 | 编写 | U1/U2 | 新增/修改控制逻辑 | `plcidf init/new` 脚手架 → 编辑 ST 与变量 CSV →（可选）`plcidf sync --dry-run` 预览 diff | 契约偏离 → 静态检查报错；UTF-16 转码失败 → 明确报错 [§2.3] | FR-L1~L4 |
| UC-02 | 编译 | U1/U2 | `plcidf build` | （如未同步先 `sync`）→ 选后端 → 环境自检 → 设备调度（[ARCH-§9]）→ 桥式驱动 AutoShop 编译 → 产物+哈希+证据落盘 | 编译失败 → 诊断列表 + 编译窗口截图 | FR-B1~B7 |
| UC-03 | 烧录 | U1/U2（人在场） | `plcidf flash` | 预检计划（产物哈希/目标/备份确认）→ 授权门禁 → 桥式驱动下载 → 程序校验 → 证据 | 缺授权 → **默认拒绝**（exit 4）；下载后 RUN 状态按 [01-D] 只记录不虚构 | FR-F1~F7 |
| UC-04 | 监测 | U1/U2 | `plcidf monitor` | 选传输（TCP/串口/虚拟，§3）→ 首连 `--calibrate` 字序判别 [00-K3] → 周期快照 → 表格 + JSONL | 假连接疑似（magic/量程异常）→ 警告并要求显式放行 [03-①④] | FR-M1~M8 |
| UC-05 | 仿真闭环 | U1/U2 | `plcidf sim` + `monitor --backend sim_virtual` | 起虚拟从站（steady/low_voltage/overload）→ 同一套监测代码采集 → 改码复跑 | 写功能码 → 从站拒绝（契约一致 [00-四]） | FR-S1~S3 |
| UC-06 | 设备管理 | U1/U2 | `plcidf device …` | `status/attach/detach/acquire/release`；AutoShop 与 WSL 互斥由设备管理器统一调度 [00-K5③] | 陈旧租约 → `stale-clean`（留证据） | FR-D1~D5 |
| UC-07 | 证据回溯 | U1/U2 | `plcidf evidence list/show` | 按 ID/时间/动作检索真机操作证据；核对产物哈希链 | 证据缺失 → 明确报缺，不编造 | FR-E1~E4 |
| UC-08 | 后端扩展 | 后端开发者 | 新传输/新协议成熟 | 实现 `BuildBackend/FlashBackend/MonitorBackend` protocol（[ARCH-§6]）→ 注册 → `plcidf backends doctor` 可见 | 未实现方法 → 注册时校验失败 | FR-X1~X3 |

---

## 6. 功能需求（FR）

> 编号规则：`FR-<域><序号>`。域：**L 源语言与工程 / B 编译链 / F 烧录链 / M 监测链 / S 仿真闭环 / D 设备管理 / E 证据 / X 扩展性 / SEC 安全**。"验收"列给出可执行的判定方式。设计落点见 [ARCH-§] 引用。

### 6.1 源语言与工程 FR-L（v0.2 新增）

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-L1 | `plcidf init` / `plcidf new <name>`：生成 ST+CSV 工程脚手架——POU 骨架（`.st`）、变量表模板（`*_variables.csv`）、AutoShop 导入表模板（UTF-16 编码注意 [§2.3]）、寄存器契约 `modbus_register_map_real32.csv` 引用；目录形态对齐 `plc/easy521/` 既有资产 | 脚手架产物通过 FR-L3 静态检查（A 级）；导入 AutoShop 打开无错（B 级） |
| FR-L2 | `plcidf sync`：把 `.st`/`*_variables.csv` 变更经桥式同步进 AutoShop 工程（导入表驱动）。默认 `--dry-run` 只出 diff 预览（改了哪些变量/正文段落），实际同步属 Windows 副作用，落证据 | 修改一个量名/阈值 → `sync --dry-run` diff 正确；实际 sync 后 AutoShop 工程更新且证据完整（B 级） |
| FR-L3 | 静态检查：ST 语法边界检查（LiteST 方言约束，如无 `LREAL` [§2.3]）+ 变量 CSV 与寄存器契约一致性校验；作为 `build --backend sim_virtual` 与 `sync` 的前置步骤 | 对契约偏离与 LREAL 误用用例报错并给出行级定位 |
| FR-L4 | **编译能力如实声明**：当前未实现原生 ST→目标应用编译；首期编译方案为 `bridge_autoshop`。静态检查与数据演示不得冒充编译；新增原生后端须经方言、ABI、链接和运行验收 | 帮助与能力清单区分静态检查、Windows 桥接和已验证原生能力；不能仅凭 ELF 目标架构判定可部署 |

### 6.2 编译链 FR-B

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-B1 | `plcidf build` 单命令编译，默认后端 `bridge_autoshop`，`--backend` 显式可选 | 在装有 AutoShop 的环境执行，产出产物目录，exit 0；`--json` 输出产物清单 |
| FR-B2 | 产物不可抵赖：每个产物记录 SHA-256，并进入后续 flash 授权短语（FR-SEC2） | 篡改产物后重跑 `flash --plan`，短语变化；校验步骤拒绝哈希不符的产物 |
| FR-B3 | 编译证据落盘：编译输出文本 + AutoShop 窗口截图；编译输出 ListBox 为 owner-drawn 读不出时**必须**退回截图 [01-E2] | 任一次 build 后 `evidence show` 可见日志与截图引用 |
| FR-B4 | `--clean` 清理中间产物后重编 | 二次执行产物哈希稳定（同输入同哈希，桥式 GUI 编译允许时间戳类差异字段排除后比对） |
| FR-B5 | 编译失败输出结构化诊断列表（`--json`）：错误文本、来源（AutoShop 输出行/截图引用）、建议动作 | 人为制造 ST 语法错误，诊断非空且 exit≠0 |
| FR-B6 | `sim_virtual` 后端提供离线静态检查（即 FR-L3 的承载点）与虚拟闭环验证包，并**明示非厂商编译、不替代 bridge_autoshop** | `build --backend sim_virtual` 对契约偏离用例报错 |
| FR-B7 | `plcidf backends doctor` 环境自检：AutoShop 安装与版本、`autoshop_ui.ps1` 可用、usbipd 存在与否、pymodbus 版本 | 缺 AutoShop 的纯 WSL 环境下 doctor 如实报告 bridge 不可用，sim/monitor 后端不受影响 |

### 6.3 烧录链 FR-F

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-F1 | `plcidf flash --artifact <file>` 经 `bridge_autoshop` 下载；`run`/`stop` 为独立命令 | 真机有人在场授权流程走通一次（已有 USB 下载通道实证 [01-C] [00-二]） |
| FR-F2 | 授权门禁：缺 `--allow-flash --operator-present --confirm <短语>` 任一项 → **默认拒绝**，exit 4，不产生任何 Windows 侧副作用 | 三组缺参负向用例全部拒绝且零副作用 |
| FR-F3 | 下载后强制程序校验（对齐档案实证流程：下载 → 厂商"程序校验"一致 [01-C]），校验失败判定 flash 失败 | 校验失败用例 exit≠0 且证据记录失败 |
| FR-F4 | 下载前预检：产物哈希复核、目标工程匹配（DEV/TEST）、备份与"下载工程源码"属性提醒 [01-C]、PLC 当前状态记录 | `flash --plan` 输出预检报告；目标不匹配时拒绝 |
| FR-F5 | `run`/`stop` 各自独立授权（`--allow-run` / `--allow-stop`），默认拒绝；失败情形（如"未能正确切换至运行状态" [01-D]）如实上报并留证据，不重试不臆断 | 软件 RUN 失败用例：exit≠0，证据含 AutoShop 原文与面板拨码检查提示 |
| FR-F6 | flash/run/stop 全程持有设备租约（[ARCH-§9]），期间 WSL 侧不得并发使用 USB；租约结束归还原状态 | flash 进行中并发 `device attach` 被拒绝 |
| FR-F7 | 下载/RUN 结果的"PLC 侧状态"只记录可证实来源（AutoShop 反馈文本/截图、指示灯人读记录），**禁止虚构运行状态** [01-D] | 证据 schema 中状态字段带 `source` 属性，抽查无臆造值 |

### 6.4 监测链 FR-M

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-M1 | `plcidf monitor` **硬编码只读**：接口面与实现面均无写能力（多层防线见 [ARCH-§8.3]） | 静态断言测试：monitor 模块源码中不存在任何写功能码调用；从站端到端写拒绝用例通过 |
| FR-M2 | `monitor_modbus` 后端：Modbus TCP 主通道（§3），默认端口 502 [01-A3]，pymodbus ≥3.16,<4 [02-2.7]，站号传 `device_id=` [03-②踩坑] | 对虚拟从站端到端读通；对真机参数仅换 `--host`（[wsl-tools/README §5] 形态） |
| FR-M3 | 首连强制字序校准：`--calibrate` 按 ABCD/BADC 双解打分 [00-K3] [01-A4]；并列时如实报"无法判别"回落 ABCD | 已知字序用例判别正确；全零寄存器用例报"无法判别" |
| FR-M4 | 输出双形态：人读表格 + `--jsonl` 结构化落盘（时间戳、原始字、解码值、量程标记） | JSONL 可被 `evidence show` 与 AI 直接解析 |
| FR-M5 | 假连接防护：首连读签名字寄存器（模拟器 D523 magic=521 [01-G]，真机用契约量程合理性 [03-④]）双读校验；疑似 TUN 劫持 → 大字警告，放行需 `--accept-unverified-transport` | 对"全应答"假服务用例触发警告且默认中止 |
| FR-M6 | `sim_virtual` 后端直连 `virtual_easy521.py` 三场景（steady/low_voltage/overload，D18 模式位） | 三场景端到端断言（沿用 wsl-tools e2e 语义，65 项测试全绿 [00-四]） |
| FR-M7 | 串口 RTU 传输用于 **RS485 + USB-485 适配器** 路径（本机通信参数和联调待验）；编程 USB 口的 15 组 RTU 探测无响应，当前不作为受支持监测传输 [RTU] | 参数装配 mock 测试；文档区分 RS485 传输与 USB 编程口，明确硬件验收状态 |
| FR-M8 | `native_usb` 后端为注册表中的预留插槽（P3 后评估 [00-五]）：`backends list` 可见、`doctor` 报告"未实现" | 插槽存在且标注清晰，不误报可用 |

### 6.5 仿真闭环 FR-S

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-S1 | `plcidf sim --scenario …` 一条命令起虚拟从站（默认 127.0.0.1:1502，免 root），参数透传 `--word-order/--tick/--log` | 场景启动后打印 READY；monitor 立即可读 |
| FR-S2 | 闭环演示命令序列（sim → monitor → 改码 → 复跑）写入 README 并可照抄执行 | 新环境从 clone 到闭环 ≤ 5 分钟，仅依赖 Python 3.12 + venv |
| FR-S3 | 虚拟与真机共用唯一契约源 `e521_regs.py`（9 量 × 2 寄存器 = 0~17，REAL32 ABCD）[00-四] | 契约偏移/编解码测试同时约束两端（既有 `test_real32.py` 语义保留） |

### 6.6 设备管理 FR-D

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-D1 | `plcidf device status/attach/detach`：查询并切换 USB 所有权（attach→WSL `/dev/ttyACM0`；detach→归还 Windows/AutoShop）[00-K5③] | 每次切换后 `status` 如实反映；attach 后 WSL 侧设备节点可见（有硬件时） |
| FR-D2 | 互斥由**设备管理器统一调度**（不允许后端各自为政）：bridge_autoshop 工作前必须持 `WINDOWS_AUTOSHOP` 租约；冲突请求被拒绝并提示持有者 | 状态机转移表测试（[ARCH-§9]）全绿 |
| FR-D3 | 租约锁落盘（PID、目的、时间戳、boot id）；进程死亡或超时 → 判定陈旧，`stale-clean` 清理并留证据 | 杀进程后 `status` 报陈旧；`stale-clean` 后可重新 acquire |
| FR-D4 | attach/detach 自身**不需授权旗标**（不触碰 PLC 状态），但**必须留证据**（[ARCH-§8.2] 策略表） | 证据流中可见 attach/detach 事件 |
| FR-D5 | 设备身份建档：`VID_0471&PID_A531`、序列、枚举类型，来自实测 [03-③] [00-K1] | `device status` 显示身份指纹，不符时告警（防错插设备） |

### 6.7 证据 FR-E

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-E1 | 一切真机副作用（flash/run/stop/attach/detach，及编译/同步这类 Windows 副作用）落 append-only JSONL 证据：时间、操作者（human/ai-session）、动作、后端、产物 SHA-256、结果、截图/日志引用 | 任一授权操作后证据条目完整；字段 schema 版本化 |
| FR-E2 | `plcidf evidence list [--action …] [--since …]` 与 `evidence show <id>`（`--json` 可选） | 按动作/时间过滤正确；单条展示含全部字段 |
| FR-E3 | 证据含 AutoShop GUI 截图引用（编译/下载/同步关键步骤），截图文件与 JSONL 同目录管理 [01-E2] | `evidence show` 给出的路径存在且可打开 |
| FR-E4 | 证据只追加不修改；提供 `evidence verify` 校验条目自洽（哈希链/字段完整性） | 篡改一条历史证据后 `verify` 报告失败 |

### 6.8 扩展性 FR-X

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-X1 | 后端注册表：内置后端静态注册，保留 entry-point 式外挂扩展点（[ARCH-§6.6]） | 注册表列出 4 个后端及状态（现役/预留） |
| FR-X2 | 新后端 = 实现 protocol + 一行注册；`cli.py` 主体零改动 | 以 `sim_virtual` 为样板走查：新增文件数 ≤ 1 + 注册 1 行 |
| FR-X3 | `plcidf backends list/doctor` 展示后端矩阵与健康度 | doctor 输出与 FR-B7 一致 |

### 6.9 安全 FR-SEC

| 编号 | 需求 | 验收 |
|---|---|---|
| FR-SEC1 | 默认拒绝矩阵（[ARCH-§8.2]）：READ 默认允许；ATTACH/DETACH 允许但强证据；WRITE/FLASH/RUN/STOP 一律需旗标+在场+确认短语；FIRMWARE（P5 预留）一律拒绝 [ARCH-§11] | 矩阵负向用例全覆盖 |
| FR-SEC2 | 确认短语绑定操作对象：`--confirm "DOWNLOAD-<sha256 前 8 位>"`（由 `--plan` 打印），换产物即换短语，防"确认后换包" | 短语与产物哈希联动用例 |
| FR-SEC3 | 授权令牌一次性：每次授权操作单独申请，**无"记住授权"开关，无全局 `--yes`** | 同一命令第二次执行仍需完整旗标 |
| FR-SEC4 | monitor 只读四层防线：①CLI 不提供写参数；②`MonitorSession` 类型面无写方法；③后端实现仅 import 读调用；④测试锁死（对齐 wsl-tools 既有做法 [wsl-tools/README §2]） | 四层各有对应测试或静态检查 |
| FR-SEC5 | AI 两步确认协议：`flash --plan` 输出计划+短语（零副作用）→ 人在对话中批准 → AI 携完整旗标执行。`--operator-present` 语义为"本会话内人已明确批准此操作"的组织约定并在证据中记录 [00-六] | 协议文档化；evidence 记录 operator 字段 |
| FR-SEC6 | 扫描/联调前置检查：对真机 TCP 操作前提示 TUN 风险 [03-①] [00-K5①]，与 FR-M5 假连接防护联动 | 首连防护用例通过 |
| FR-SEC7 | **固件级实验默认禁止接触真机**（P5 研究轨，[ARCH-§11]）：任何固件升级通道/调试口/底层总线实验须用户**单独授权**（不复用 flash/run 的授权）且变砖风险前置声明；P5 立项前相关命令面不存在 | 代码库中无固件写路径；红线声明出现在 P5 相关文档卷首 |

---

## 7. 非功能需求（NFR）

### 7.1 安全（SEC）

| 编号 | 需求 |
|---|---|
| NFR-SEC1 | **默认拒绝**：任何未被明确允许的真机写副作用，系统在设计层面不可达（不是"提示后可绕过"）。对齐红线 [00-六]。 |
| NFR-SEC2 | 只读能力与授权能力**代码隔离**：monitor 路径不 import 任何具备写能力的模块（[ARCH-§8.3]）。 |
| NFR-SEC3 | 证据具备防篡改倾向：append-only、条目哈希链；产物哈希贯穿 build→flash 全程（FR-B2/E4）。 |
| NFR-SEC4 | 失败封闭（fail-closed）：环境自检失败、设备冲突、字序不可判别、假连接疑似时，默认中止而非降级继续。 |

### 7.2 可靠性（REL）

| 编号 | 需求 |
|---|---|
| NFR-REL1 | 后端故障隔离：单后端崩溃不影响其他命令与其他后端（进程级或异常边界，[ARCH-§5]）。 |
| NFR-REL2 | 退出码语义稳定可编程：0 成功 / 2 用法错 / 3 环境不满足 / 4 授权拒绝 / 5 后端失败 / 6 设备冲突（[ARCH-§6.7]），AI 依赖其分支。 |
| NFR-REL3 | 软件闭环可回归：`wsl-tools` 65 项测试语义全部保留并纳入 CI 可跑集合 [00-四]。 |
| NFR-REL4 | 桥式失败可恢复：AutoShop 弹窗/超时按 `autoshop_ui.ps1` 的 accept/inspect 能力处理 [01-E2]；失败后不留租约锁、不破坏 GUI 可用状态。 |
| NFR-REL5 | 反假数据：监测值经量程/字序/签名三重校验，异常数据**带标记呈现**而非静默丢弃 [03-④]。 |

### 7.3 可移植性（PORT）

| 编号 | 需求 |
|---|---|
| NFR-PORT1 | WSL2 Ubuntu（NAT 模式）+ Python 3.12 + `.venv` 内依赖（pymodbus ≥3.16,<4、pyserial、pytest）即可跑 sim/monitor 闭环 [03-附] [02-2.7]。 |
| NFR-PORT2 | 桥式后端要求 Windows 10/11 + AutoShop V4.11.x + PowerShell 5.1+（`autoshop_ui.ps1` 兼容面 [01-E2]）+ usbipd-win；缺失时 doctor 如实报告，其余功能不受累。 |
| NFR-PORT3 | 后端依赖懒加载：不装 AutoShop/usbipd 的环境 import `plc_idf` 不报错，仅相应后端不可用。 |
| NFR-PORT4 | 零 root 运行：仿真用 1502 等非特权端口；502 仅作为真机目标端口由用户显式给出。 |
| NFR-PORT5 | 网口直连（P4）不引入新框架：`monitor_modbus` 仅换 `--host/--port`，与 [wsl-tools/README §5] 切换表一致。 |

### 7.4 可观测性（OBS）

| 编号 | 需求 |
|---|---|
| NFR-OBS1 | 每条命令支持 `--json`（机读）与人读两种输出；AI 优先消费 `--json`。 |
| NFR-OBS2 | 长操作（build/flash/sync）产生进度事件流（ProgressSink，[ARCH-§6.1]），事件同步写入证据。 |
| NFR-OBS3 | 全链路可追溯：一条 trace id 贯穿 plan → 授权 → 执行 → 证据条目。 |
| NFR-OBS4 | `backends doctor` + `device status` + `evidence verify` 构成三件套体检，可在 1 分钟内判定环境健康。 |

---

## 8. 约束与假设

### 8.1 当前实现约束与项目政策（可随证据更新）

| 编号 | 约束 | 出处 |
|---|---|---|
| C1 | 首期选用 AutoShop Windows 编译后端；历史调研未找到可直接用于 Easy521/LiteST 的公开原生编译链。autoshop-mcp 的 H3U 先例不构成本目标已适配证明；本机 ARM ELF 中间产物提供进一步研究线索 | [02-2.4] [01-E] [02-②]；2026-10-17 底层复核 |
| C2 | 项目尚未掌握厂商程序下载/在线调试协议的可复用实现；不能据检索未发现推出全网不存在 | [02-1.4] [02-1.5] [01-A2] |
| C3 | 档案记录编程 USB 为 CDC/bulk 拓扑，原始描述符待补；编程协议未知；15 组 RTU 探测无响应，当前不支持该路径 | [00-K2] [RTU]；2026-10-17 底层复核 |
| C4 | REAL32 跨寄存器字序官方零记载；契约 ABCD 未经真机验证，运行时必须可校准 | [01-A4] [00-K3] |
| C5 | 真机 IP 无任何记录（工程文件厂商混淆格式不可读） | [01-F] [00-K4] |
| C6 | 代理 TUN 开启时 TCP 假连接；Hyper-V 防火墙默认丢弃 WSL→Windows 入站 | [03-①] [00-K5①②] |
| C7 | USB 挂载与 AutoShop 使用互斥（attach/detach 切换） | [00-K5③] |
| C8 | 近期单 USB Type-C 线，网口待拓展坞（下半月） | [00-一] [00-五P4] |
| C9 | GUI 驱动脚本不得作为无人监督的硬件部署脚本 | [01-E2] 引档案 §7 |
| C10 | RUN/STOP 面板拨码与软件按钮关系未证实；3 次软件 RUN 失败根因待现场确认 | [01-D] [00-K6] |
| C11 | 20256/20257 端口传闻不得进入任何设计假设 | [02-1.3] |
| C12 | **许可证 = PolyForm Noncommercial 1.0.0（非商用）**；依赖库保留各自许可证（pymodbus BSD-3、pyserial BSD 类、autoshop-mcp MIT 仅作方法参考不复制代码等），引入新依赖须核对兼容性 | 本条为项目决定（v0.2 新增） |
| C13 | LiteST 为方言：不支持 `LREAL`（工程用 32 位 REAL）、单文件 ≤1000 行、版本门槛（Easy 固件 ≥5.67.0.0、AutoShop ≥4.8.1.0）[plc/easy521/README.md；01-附页码校正 p227]；AutoShop 4.11 Unicode 导入用 UTF-16 | [01-附] [§2.3] |

### 8.2 假设（待验证，失效时的对策）

| 编号 | 假设 | 失效对策 |
|---|---|---|
| A1 | Windows 主机常驻 AutoShop V4.11.0.5 且用户持有合法工程（DEV/TEST） | bridge 后端不可用 → 人工编译兜底（现状流程） |
| A2 | 授权类操作均有真人在场（组织约定；`--operator-present` 为声明式） | 门禁退化为纯技术控制，须在证据中显式标注"无在场声明"并默认拒绝 |
| A3 | D0-D17 已按契约映射且工程侧维持该映射 | `--calibrate`/量程校验告警；回退人工核对 CSV 契约 |
| A4 | usbipd 可从 WSL 触发 Windows 侧命令（策略允许） | `device attach/detach` 降级为"打印待手工执行的命令" |
| A5 | 软件 RS485 参数默认 9600 8N1，不代表设备出厂组态；手册记录本体 1 路，实物接线及本机通信参数待确认（§3） | 使用 `--baud/--parity/--stopbits` 与设备组态对齐，真机读通后再验收 |
| A6 | AutoShop 导入表格式随版本稳定（4.11 已实证 [§2.3]） | 版本变更须重新核对原生格式并回归 `sync`（FR-L2） |

---

## 9. 验收标准

> 分级：**A 级** = 无真机、纯 WSL 可验收；**B 级** = 需 Windows+AutoShop；**C 级** = 需真机/网口（P4 后补验）。

| 编号 | 场景 | 级 | 通过判据 | 关联 FR |
|---|---|---|---|---|
| AC-1 | 新环境从 clone 到仿真闭环 | A | `sim`（steady）+ `monitor --backend sim_virtual --once --calibrate` 输出 9 量表，≤5 分钟，exit 0 | FR-S1~S3, M6 |
| AC-2 | 只读不可绕过 | A | 四层防线用例（FR-SEC4）全绿；对虚拟从站发写码被拒 | FR-M1, SEC1/4 |
| AC-3 | 授权默认拒绝 | A/B | `flash` 无旗标 → exit 4、零副作用；缺在场/短语同理 | FR-F2, SEC1 |
| AC-4 | 确认短语绑定产物 | A | 篡改产物后 `--plan` 短语变化，携带旧短语执行被拒 | FR-SEC2 |
| AC-5 | 桥式编译全流程 | B | `build` 产出产物 + SHA-256 + 编译截图证据；人为错误用例给出诊断 | FR-B1~B5 |
| AC-6 | 桥式烧录全流程 | C | 有人在场授权下载 → 程序校验一致 → 证据链完整；RUN 状态按 C10 如实记录 | FR-F1~F7 |
| AC-7 | 设备互斥 | B | bridge 持租约期间 `device attach` 被拒（exit 6）；杀进程后陈旧锁可清理 | FR-D2/D3 |
| AC-8 | 证据链与防篡改 | A | `evidence list/show/verify` 三命令可用；篡改用例被 `verify` 发现 | FR-E1~E4 |
| AC-9 | 后端扩展样板 | A | 以预留 `native_usb` 插槽验证注册表机制；`backends list/doctor` 输出正确状态 | FR-X1~X3, M8 |
| AC-10 | AI 闭环可用性 | A/B | 全命令 `--json` + 稳定退出码 + `--plan` 两步协议可被脚本化走通（模拟 AI 会话脚本） | NFR-OBS1, REL2, SEC5 |
| AC-11 | 环境体检 | A/B | `backends doctor` 在无 AutoShop 环境正确降级报告；`device status` 反映真实状态 | FR-B7, NFR-OBS4 |
| AC-12 | 真机监测首连（P4 补验） | C | 插网线 → `monitor --host <IP> --port 502 --calibrate` 判定字序并持续采集；顺带确认 RUN 拨码根因 [00-五P4] | FR-M2/M3/M5 |
| AC-13 | 语言层脚手架与静态检查（v0.2） | A | `plcidf init/new` 产物通过静态检查；LREAL 误用与契约偏离用例被行级定位报错 | FR-L1/L3/L4 |
| AC-14 | ST/CSV 同步（v0.2） | B | 修改量名/阈值 → `sync --dry-run` diff 正确 → 实际同步后 AutoShop 工程更新且证据完整 | FR-L2, E1 |

---

## 10. 术语表

| 术语 | 定义 |
|---|---|
| PLC-IDF / `plc_idf` / `plcidf` | 产品名 / Python 包名 / CLI 命令名；`py2plc` 保留为兼容别名（v0.2 更名，命名对标 ESP-IDF） |
| ST（结构化文本） | IEC 61131-3 的文本化 PLC 语言，本项目首期选定前端（§2） |
| LiteST | AutoShop 的 ST 方言（无 `LREAL`、单文件 ≤1000 行等，C13） |
| LD/FBD | IEC 61131-3 的图形语言（梯形图/功能块图）；相应自动生成和导入链尚未验证，不纳入首期（§2.1） |
| 导入表 | AutoShop 变量表的原生 CSV 导入格式（UTF-16，已实证 [§2.3]） |
| sync | `plcidf sync`：ST/CSV 变更经桥式同步进 AutoShop 工程（FR-L2） |
| 桥式后端（bridge） | WSL 编排、Windows 执行的后端形态；Windows 承担"用户不可见的编译服务器"角色 [00-核心判断] |
| 原生后端（native） | 在 WSL/Linux 内直接完成的后端（monitor_modbus、将来的 native_usb） |
| 授权门禁 | 旗标 + 在场声明 + 确认短语三要素的统一闸门（[ARCH-§8]） |
| 确认短语 | 由操作计划（含产物哈希）派生的字符串，用户须逐字回传（FR-SEC2） |
| 设备租约 | 设备管理器签发的独占使用权凭据（[ARCH-§9]） |
| 证据（evidence） | 一切真机/Windows 副作用的 append-only 结构化记录（FR-E1） |
| 契约 | `e521_regs.py` + `modbus_register_map_real32.csv` 定义的寄存器/字序约定 [00-四] |
| 字序校准（calibrate） | ABCD/BADC 双解打分判别真机浮点字序的过程 [01-A4] |
| P5 研究轨 | 底层/固件研究线；当前开展离线研究，任何真机固件/调试口操作须单独授权（[ARCH-§11]） |

## 11. 需求追溯

| UC | FR 域 | ARCH 设计落点 |
|---|---|---|
| UC-01 编写 | FR-L1~L4 | [ARCH-§3] [ARCH-§6.5] |
| UC-02 编译 | FR-B1~B7 | [ARCH-§6.2] [ARCH-§7] |
| UC-03 烧录 | FR-F1~F7 | [ARCH-§6.3] [ARCH-§8] |
| UC-04 监测 | FR-M1~M8 | [ARCH-§6.4] [ARCH-§8.3] [ARCH-§4] |
| UC-05 仿真闭环 | FR-S1~S3 | [ARCH-§6.4]（sim_virtual） |
| UC-06 设备管理 | FR-D1~D5 | [ARCH-§9] 状态机 |
| UC-07 证据回溯 | FR-E1~E4 | [ARCH-§8.4] |
| UC-08 后端扩展 | FR-X1~X3 | [ARCH-§6.6] [ARCH-§11] |
| （横向）源语言层 | FR-L1~L4 | [ARCH-§3] |
| （横向）监控接口事实 | FR-M2/M7 | [ARCH-§4] |
| （横向）安全 | FR-SEC1~7 | [ARCH-§8] |
| （横向）演进 | — | [ARCH-§11] 路线（含 P5 研究轨） |
