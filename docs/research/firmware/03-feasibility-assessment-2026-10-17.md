# PLC-IDF 的平台路线与底层固件可行性复核

复核日期：2026-10-17（Asia/Shanghai）。范围：仓库档案、既有厂商编译产物的离线静态检查、公开一手资料。未连接 PLC，未调用 Windows/AutoShop，未切换 USB 所有权，未读取设备存储，未刷写固件，未尝试绕过密码或签名。本次不修改 2026-10-16 的历史调查记录。

## 1. 决策结论

用户真正需要的是可由 AI 调用、可在终端复现的 PLC 开发闭环：文本源码 → 验证 → 目标构建 → 应用下载 → 运行观测 → 可追溯证据。这种体验可先经 WSL 编排与 Windows 厂商后端实现；它并不要求先替换 PLC 操作系统。完全去掉 Windows 是另一项平台能力，必须单独验收，不能把 Windows 后台执行称为原生 Linux 编译。

建议路线为：**现有 Easy521 继续推进厂商工具桥接；原生厂商适配作为有期限的研究；开放 runtime 在自控硬件上独立验证；替换 Easy521 固件保留为条件尚未满足的实验研究。** 四条路线应共享项目模型、命令接口和证据格式，但构建产物与设备兼容性不能互相冒充。

本次新增的重要离线事实：仓库现有 AutoShop `STTemp/MAIN.o` 是 ARM ELF 可重定位目标文件，DEV 目标属性为 ARMv7 Application、Thumb-2、VFPv3、NEONv1。它提供了比 USB 字符串更直接的**应用编译目标证据**，仍不能确认实际 SoC 型号与运行 OS。完整命令输出、源文件 SHA-256 与工具版本见 [离线 ELF 检查记录](20261017-offline-elf-inspection.json)。

## 2. 事实、推断与待验证项

标注规则：**事实**表示可在本次列出的文件或一手页面直接复核；**推断**表示依据事实得到的架构判断；**TBD**表示缺少必要证据。历史运行结果只对其记录时间与条件负责。

| 编号 | 结论 | 等级与证据边界 |
|---|---|---|
| E1 | 2026-09-28 AutoShop 经 USB 下载 Easy521 DEV 工程成功，厂商一致性检查相符；随后两次 RUN 切换失败，监视值处于 STOP | **事实：历史 GUI 档案**，[hardware_usb_stage_20260928.json](../../../plc/easy521/evidence/hardware_usb_stage_20260928.json)。证明厂商下载路径曾工作，不证明新工具已自动闭环，也不证明控制算法已在真机运行 |
| E2 | 2026-10-16 13:12 的 Windows 快照记录 `0471:A531`、`PlcUsb`、无 COM 映射，当时 WSL 无 USB 透传 | **事实：历史快照**，[usb_inventory.json](../../../wsl-probe/usb_inventory.json)。Windows 驱动类名不能单独证明设备描述符不是 CDC |
| E3 | 同日较晚的脚本与 JSON 记录 `/dev/ttyACM0` 可打开，10 组波特率/站号组合及 5 组校验/停止位组合的 FC03 均无响应 | **事实：历史探测记录**，[第一轮](../../../wsl-probe/usb-rtu/rtu_probe_result.json)、[第二轮](../../../wsl-probe/usb-rtu/rtu_probe_result_parity.json)。未保存原始 USB 描述符与完整透传过程，不能将 E2、E3 当作同一时刻状态 |
| E4 | 汇总档案记录 `Easy52x USB Device v2.4`、`Linux 3.10.65 with sunxi_usb_udc` 及 CDC/bulk 端点拓扑 | **事实：文档有此记录；原始描述符 TBD**，[历史汇总](../../wsl-feasibility/00-synthesis-and-roadmap.md)。本次仓库检索未找到对应 `lsusb -v`/sysfs 原始输出，不能直接提升为硬件终审证据 |
| E5 | DEV/TEST/BENCH 的 `STTemp/MAIN.o`，以及 DEV 的 `Temp/stprog.o`，均为 ELF32、小端、ARM、EABI5；DEV 的属性包括 ARMv7 Application、Thumb-2、VFPv3、NEONv1 | **事实：本次离线静态检查**，[检查记录](20261017-offline-elf-inspection.json)。描述的是归档目标文件，不是本次厂商新构建或设备执行结果 |
| E6 | DEV `MAIN.o` 包含 `MAIN` 以及未定义的全局变量、`_HC_*`、`_ST_*` 等符号 | **事实：符号表**。**推断：**其最终链接或加载依赖外部符号、变量布局与厂商约定；不能把裸 `.o` 等同于可直接下载的完整工程 |
| E7 | 汇川官方开发者平台列出 H5U&Easy 固件资源，区分 `.upgrade` 升级包、`.img` 烧录包，要求按设备、软件与版本匹配选择 | **事实：本次官方页面复核**，[资源页](https://www.inoplanet.com/developerResources/details/firmware-001-002)。本次未下载二进制样本，包内结构、签名与具体机型可用性仍为 TBD |
| E8 | 内部实际 SoC、内核配置、BSP、根文件系统、bootloader、存储布局、FPGA/I/O 接口与安全启动状态 | **TBD**。厂商手册的通信能力、USB 自述与应用目标文件均不足以独立给出完整答案 |

离线复核命令（只针对仓库文件，可复现）：

```bash
file plc/easy521/autoshop/BESS_E521_DEV/STTemp/MAIN.o
readelf -h plc/easy521/autoshop/BESS_E521_DEV/STTemp/MAIN.o
readelf -A plc/easy521/autoshop/BESS_E521_DEV/STTemp/MAIN.o
readelf --wide --symbols plc/easy521/autoshop/BESS_E521_DEV/STTemp/MAIN.o
```

DEV `MAIN.o` SHA-256：`10b84e22569143dd04e24490d8d33d05b004fa383f60e3350fc7a20988627ea9`。ELF Header 的 `UNIX - System V` 是 ELF ABI 字段，**不能据此判定目标一定运行 Linux**。

## 3. 对历史调查的勘误

| 历史表述 | 本次采用的准确表述 | 原因 |
|---|---|---|
| USB 字符串“锁死全志 Linux 3.10 BSP”，已确证内部 OS | 字符串与 Linux gadget/sunxi 软件栈相符，是有价值的平台线索；实际 OS、芯片型号与来源仍待独立证据 | Linux 官方示例允许配置厂商和产品字符串；自述并非硬件认证。[USB gadget configfs](https://docs.kernel.org/usb/gadget_configfs.html) |
| F1C100s/F1C200s 为仍可优先考虑的候选 | 撤回按营销功能、网口数与内核版本排序 SoC 的做法；归档应用目标为 ARMv7 Application，应优先核对这一编译目标 | ARMv7-A 目标与历史 ARM9 候选假设不一致；尚需确认目标文件确由该机型 runtime 直接执行，不能把属性直接变成 SoC 丝印 |
| “15 组合静默，RTU 证伪” | 这 15 个条件下未观察到 Modbus RTU FC03 响应；USB 口没有已验证的通用 RTU 监测能力 | 超时不能排除其他站号、前置握手、配置开关或不同协议。没有成功证据就不能作为产品能力，但也不是穷举不可能性证明 |
| Windows 无 COM，所以设备不是 CDC | 快照只证明当时 Windows 驱动没有 COM 映射；较晚 WSL 记录与 CDC 识别相符 | USB 描述符、主机驱动绑定、应用层协议属于不同层次 |
| 全网零文档、零逆向、唯一实现、没有合法捷径 | 截至本次检索，在已查的一手来源中，尚未确认可直接支持 Easy521/LiteST 的 Linux 原生编译与下载实现 | 有限检索不能证明全网不存在；平台未来支持、私有 SDK 或尚未检索到的实现都可能改变结论 |
| FEL 永远能救砖、没有证据表明禁用所以可用 | U-Boot 文档描述通用 Allwinner FEL；Easy521 的 SoC、端口布线、启动策略、安全配置与恢复能力未验证 | 平台通用功能不等于整机暴露能力；ROM 本身不可擦除也不保证整机能恢复。[U-Boot Allwinner](https://docs.u-boot.org/en/latest/board/allwinner/sunxi.html) |
| 找不到签名资料，所以固件大概率无签名、验证码是非密码学防线 | 固件签名、加密、验证码机制与 secure boot 状态均 TBD | 资料缺失不能证明机制不存在；验证码也可能参与加密认证，不能凭名称分类 |
| 官方升级抓包、开盖“零风险” | 被动抓包和官方升级是两个动作；采集本身不写设备，但升级有独立状态变更与失败风险；开盖也有机械、电气与保修影响 | 研究计划应分别评估观察动作与被观察操作，不把恢复路径当作已验证保障 |
| 比较包大小或压缩比即可判断加密，SD 烧录证明 TF 槽由 BootROM 启动 | 大小/熵只能给出格式线索；SD 更新也可能由既有固件/bootloader 读取 | 不能据单一现象推出完整加密或启动链结论 |
| 无公开漏洞通告，所以产品尚未被安全研究触碰 | 本次未核实具体 Easy521 公告，不据此推断研究覆盖率或安全性 | 不可见的研究与未公开问题无法由搜索排除；漏洞与本项目的应用编译可行性也不是同一判断 |

旧文档保留其日期语境；后续需求、架构与对外说明引用本复核的证据等级，避免继续传播上述绝对结论。

## 4. 从第一性原理拆解“找到芯片后直接编译”

ESP-IDF 的参考价值是厂商提供一致的工具链、SDK、目标配置、构建、下载与日志接口。乐鑫官方说明 `idf.py build` 生成应用及相关引导产物，`flash monitor` 可串联部署与日志；这些能力来自受支持的平台接口与板级适配。[ESP-IDF Linux/macOS 工作流](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/linux-macos-start-project.html)

针对现有 PLC，需要分别满足以下契约。下面是工程分解，不能视作该设备已有开放接口的声明。

| 层 | 所需资料或实现 | 只知道 CPU 为什么不够 |
|---|---|---|
| 源语言与工程前端 | LiteST 方言、类型/时间/保持变量语义、工程与设备配置、库接口 | 标准 ST 与厂商方言不保证语义和库兼容；Python→ST 也不自动完成变量分配与工程生成 |
| 应用编译与链接 | 指令集、ABI、编译选项、符号、内存布局、重定位、厂商产物格式 | ARM 编译器能生成对象文件，仍需正确对接 runtime 与工程封装；现有 E5/E6 已提示这一差距 |
| 应用下载与状态控制 | 设备识别、握手、版本/机型检查、完整性验证、下载提交、状态读回、恢复策略 | USB/以太网只是承载字节，CDC/bulk 不定义这些应用层语义 |
| PLC runtime | 扫描任务、输入/输出映像、定时器、保持区、异常与看门狗、在线变更/监控协议 | “可执行 ARM 程序”并不等于保持 PLC 的扫描语义与故障行为 |
| OS 与板级支持 | 目标内核/RTOS、设备树或板配置、网络/存储、I/O 与可能的协处理器接口 | 同系列芯片的通用 BSP 不包含 OEM 板卡连接与私有驱动；当前无证据确认该机的 FPGA 分工 |
| 引导与恢复 | 引导链、分区、镜像格式、签名策略、官方恢复材料及可验证回退 | 找到 BootROM 工具不等于可以运行自编镜像；恢复通道须针对具体整机验证 |
| 时序与物理行为 | 周期、最坏延迟、抖动、超期策略、输出失效状态、通信中断行为 | Linux 可启动或 `SCHED_FIFO` 可设置，都不能单独证明控制时限满足 |

**可编译、可装载、可运行、可控制真实 I/O、满足实时性是五个独立里程碑。** 新后端需按这五项分别给证据。

Linux PREEMPT_RT 官方文档解释了减少不可抢占区域与调度延迟的机制；它不是整机时限认证。若替换 runtime 或系统，应在目标板、最坏 I/O/网络负载与故障条件下测量扫描分布、超期次数、看门狗与输出状态；WSL 内仿真不能替代目标板实时性验证。[Linux 实时内核原理](https://docs.kernel.org/next/core-api/real-time/theory.html)

## 5. 四条路线的证据、验收与退出门槛

以下是立项建议与工程判断，不是厂商承诺。研究路线不能因理论上可行就登记为“实现完成”。

| 路线 | 当前证据与可交付能力 | 首个通过门槛 | 退出/降级门槛 |
|---|---|---|---|
| A. WSL CLI + Windows 厂商桥接 | **最强的现有设备证据**：AutoShop USB 下载历史成功；现有 GUI 脚本和项目资产可复用。目标是用户在终端完成流程，Windows 承担厂商工具执行 | 从干净项目副本导入文本，确认为本次源码完成厂商构建；生成日志、产物 SHA-256、工具版本；在允许的实验条件下完成下载与厂商读回；运行验证另列 | 若只能按快捷键但无法可靠读出错误/目标项目，不能宣称自动 build/flash；若 GUI 会话条件不满足，返回明确不支持，保留诊断；需人在 Windows 做一次恢复也应如实报告 |
| B. 现有厂商 runtime 的原生适配 | **研究可行，未验证**：对象文件提供部分 ABI 线索；USB 拓扑仅是传输线索。原生编译器与原生下载器必须分别交付 | 先列厂商公开 CLI/SDK/授权接口与可用性；若走独立实现，先有格式/协议说明和离线 golden fixture，再做只读设备识别；编译必须通过方言与行为对照，下载须有独立读回 | 建议以一次限定研究周期作为评审点；若缺少接口授权/资料、目标 ABI、可验证产物或恢复条件，则维持桥接，不把后端名字改为 native 来掩盖 Windows 调用；只打通下载不宣称打通编译 |
| C. 自控硬件 + 开放 runtime | **开放项目已证明工具链/runtime 模式存在**：Beremiz 有 CLI 与 runtime；OpenPLC 有 Linux runtime 与 API。可实现真正受控的 Linux 原生开发闭环 | 先在本机/隔离目标运行最小 ST 程序，保存构建与执行证据；随后在选定硬件验证公开 I/O 驱动、掉线失效状态与周期要求；通过相同抽象 CLI 调用 | 不存在 Easy521 兼容层证据时不得向其下载；若目标硬件无合适 I/O 驱动或时序不达标，调整硬件/周期或停止该用例，不据“跑在 Linux 上”宣布工业适用 |
| D. 替换 Easy521 底层系统/固件 | **目前证据不足**：芯片型号、板级资料、引导策略、runtime 与 I/O 接口均 TBD；即使应用 ELF 为 ARMv7，也不补齐这些条件 | 第一阶段仅离线：取得匹配版本的官方包、来源/授权与哈希清单，确认可识别结构、CPU 目标、分区/启动材料与可获得源码；之后须先有针对该机型的可信恢复与硬件适配证据，才讨论目标板实验 | 任一关键环节依赖绕过未知口令/签名、猜测电气接口、破坏启动介质或未验证恢复，则停在资料阶段；关键 I/O/协处理器驱动不可获得时不把“启动通用 Linux”当作 PLC 替代成果 |

路线 B 的现实线索 [autoshop-mcp](https://github.com/JonathanChan-geek/autoshop-mcp) 是作者的一手实现声明：调用本机 Windows AutoShop DLL 做版本绑定的 H3U 无头编译，明确不含 ST/H5U，也不提供 PLC 下载/RUN/STOP。它可指导分层与指纹验证，尚不能证明 Easy521 的 Linux 原生编译或无头编译已经可用。

路线 C 的可核验候选：

- [Beremiz](https://github.com/beremiz/beremiz)：官方仓库明确列出终端构建/控制 CLI 和目标 runtime，适合评估命令行开发闭环。
- [matiec](https://github.com/beremiz/matiec)：官方仓库的 `iec2c` 将文本 ST/IL/SFC 转为 ANSI C。它提供语言工具能力，不承诺 LiteST 方言、Easy521 runtime ABI 或私有下载格式兼容。
- [OpenPLC Runtime v4](https://github.com/Autonomy-Logic/openplc-runtime)：官方仓库有无头 runtime、部署 API、变量调试与 I/O 插件机制，可用于自控目标的原生后端验证。版本、依赖、目标资源、许可证与 API 稳定性应在选定 commit 后复核；该仓库存在不代表能安装到 Easy521。
- [OpenPLC v3](https://github.com/thiagoralves/OpenPLC_v3) 是另一版本线；比较时固定版本，不混用其安装/API/编译器假设。

## 6. 下一批具体任务

| 任务 | 建议责任角色 | 输入与交付物 | 验收与边界 |
|---|---|---|---|
| FW-01 证据去歧义 | 平台/驱动工程师 | 为 USB 描述符、设备硬件标识、主机驱动绑定与 usbipd 状态定义采集清单；历史 E2/E3 按时间排序 | 先整理已有材料；后续获准采集时保存原始描述符、工具版本、时间、设备匹配依据与哈希。不得把设备友好名当 OS/SoC 证据 |
| FW-02 厂商接口问询材料 | 工具链工程师 | 汇总 Easy521/LiteST 编译 CLI、离线 SDK、工程导入/导出、下载/监控接口、Linux 支持、BSP/开源许可源码包的具体问题清单 | 先在仓库保存材料；实际向外发送须由用户执行或另行授权。本次未联系厂商 |
| FW-03 应用 ABI 离线清单 | 编译器工程师 | 扩展现有 ELF 检查，记录导出/未定义符号、目标属性、链接阶段与文件封装的可见信息；只使用用户有权使用的项目副本 | 区分观察事实与 ABI 假设；不运行未知二进制、不修改原厂组件、不绕过受保护工程；输出“还缺什么”而非臆造完整格式 |
| FW-04 官方固件样本台账 | 嵌入式平台工程师 | 若官方渠道允许获得样本，归档来源、机型、版本、文件长度、SHA-256、许可/获取条件；在隔离副本离线检查结构和可见标识 | 优先申请/获取材料，不需要先刷设备；本次仅验证官方资源页，没有实际固件样本，不宣称已解包或确认内部 OS |
| FW-05 开放后端小验证 | Linux/PLC runtime 工程师 | 选定一个固定 commit 的开放 runtime，最小 ST 示例、终端构建/部署/变量观测说明与证据；初期运行在隔离软件环境 | 与 Easy521 独立标识；不安装到现有 PLC；先证明语言与接口闭环，实时性与物理 I/O 留到选定硬件后验证 |
| FW-06 四路线评审 | 架构负责人 + QA | 对照第 5 节，记录已过门槛、缺失证据、投入上限、继续/回退决定 | 主线交付不依赖 D；研究未知项不包装成排期确定的功能承诺 |

研究取证优先级：现有产物与官方资料 → 厂商公开接口/源码材料 → 获准取得的固件离线分析 → 独立实验台架验证。无需为了解决终端开发体验而先启动固件升级。

## 7. 一手来源复核台账

以下均于 **2026-10-17** 检索/核读；链接为直接支持相应事实的页面。动态内容以访问时状态为准，工程实施前固定版本或 commit。

| 来源 | 本次核实内容 | 获取状态 |
|---|---|---|
| [ESP-IDF 入门](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/index.html) / [Linux 终端流程](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/linux-macos-start-project.html) | 跨平台工具链、构建/下载/监控命令的参考模式 | 官方文档可读；未运行 ESP-IDF |
| [汇川 H5U&Easy 固件资源](https://www.inoplanet.com/developerResources/details/firmware-001-002) | `.upgrade`/`.img`、匹配原则与更新方式 | 直接打开最初报错；站内搜索索引返回完整官方正文；未取得下载文件 |
| [Linux USB gadget configfs](https://docs.kernel.org/usb/gadget_configfs.html) | USB manufacturer/product 等字符串可由软件配置 | 官方文档可读；用于限定 USB 自述证据强度 |
| [Linux v3.10 gadget serial 源码](https://github.com/torvalds/linux/blob/v3.10/drivers/usb/gadget/serial.c) | 通用 gadget serial 也有 `v2.4`、CDC 配置；不能把版本字符串当芯片型号 | 官方上游源码可读；未据此认定该机使用此原版代码 |
| [U-Boot Allwinner 文档](https://docs.u-boot.org/en/latest/board/allwinner/sunxi.html) / [sunxi-tools](https://github.com/linux-sunxi/sunxi-tools/blob/master/README.md) | 通用 sunxi 引导/FEL 工具背景 | 上游文档可读；只作平台知识，未验证 Easy521 适用性 |
| [Linux 实时内核原理](https://docs.kernel.org/next/core-api/real-time/theory.html) | PREEMPT_RT 的延迟机制背景 | 官方文档可读；不构成产品实时性结果 |
| [autoshop-mcp](https://github.com/JonathanChan-geek/autoshop-mcp) | 作者声明的 H3U、Windows DLL 与功能边界 | 官方项目仓可读；第三方项目自身声明，未在本机集成验证 |
| [Beremiz](https://github.com/beremiz/beremiz) / [matiec](https://github.com/beremiz/matiec) | CLI/runtime 与 IEC 文本到 C 的可用开源方向 | 官方项目仓可读；未执行安装或构建 |
| [OpenPLC v4](https://github.com/Autonomy-Logic/openplc-runtime) / [v3](https://github.com/thiagoralves/OpenPLC_v3) | 受控硬件上的开放 runtime 方案 | 官方项目仓可读；未验证 Easy521 兼容性 |

本次厂商检索词包含 `site.inovance.com Easy521 AutoShop 编译 命令行 SDK Linux`、`site.inoplanet.com H5U Easy 固件 upgrade img`。这些有限检索未确认面向 Easy521/LiteST 的 Linux 原生完整 SDK；结论是“尚未找到已验证实现”，不是“不存在”或“永远不可能”。
