# WSL 可行性侦察 · 01 协议与资产硬事实

- 调查日期：2026-10-16。来源仅限本机资料（E 盘官方手册 PDF + py2plc 工作区档案），未联网。
- 手册路径前缀 `MAN=` `/mnt/e/Software/AutoShop/Manual/H5U&Easy_Manual/`。
  - `MAN/H5U&Easy系列可编程逻辑控制器编程与应用手册.PDF`（下称【编程手册】，994 页；任务原文写的"编程手册.pdf"在本机不存在，实为该文件）
  - `MAN/Easy系列可编程逻辑控制器用户手册.PDF`（下称【Easy手册】，123 页，第 5 章 p99-123 为 Easy521/522/523）
  - `MAN/H5U USB驱动安装操作说明.pdf`（7 页，正文仅 1 页文字，其余截图）
- 页码约定：编程手册与 Easy 手册的 PDF 页号与页脚印刷页码一致（抽查 p45、p102 均吻合），引用直接用页码。
- 标注规则：【手册】= 手册明说；【档案】= 本机工程文件/报告记录；【推断】= 由以上事实推理，非原文。

---

## A. Easy521 以太网口与 Modbus TCP

### A1. 硬件规格
- 【手册】Easy521-0808TN 集成 **2 路 RJ45 以太网口**（EtherNET1/EtherNET2）；"H5U 集成 1 路以太网口，Easy320 和 Easy52X 系列集成 2 路"【编程手册 p392】【Easy手册 p102-103 部件说明⑫】
- 【手册】RJ45 接口规格：10Mbps(10BASE-T)/100Mbps(100BASE-TX) 自适应，基带，星形拓扑，5 类以上双绞线（推荐带铝箔+编织屏蔽），节点间 ≤100m，**连接数 31**【编程手册 p392 表】
- 【手册】以太网功能总述："支持 EtherNet/IP、ModbusTCP、Socket，**程序上下载和固件升级**"【Easy手册 p104 一般规格】
- 【手册】网线要求：5 类屏蔽双绞线、带铁壳注塑水晶头【Easy手册 p117-118】

### A2. AutoShop 网口编程/下载通道
- 【手册】AutoShop 通过"工具→通信设置"，通信类型选"以太网"，填设备 IP，可 PING/测试；连接成功后即可"程序下载、上载、监控或在线修改"【编程手册 p46-47】
- 【手册】未知 IP 时可"搜索"局域网内 PLC：经交换机只能搜同网段，网线直连可跨网段【编程手册 p48】
- 【手册】修改 IP/设备名：连接后"修改IP/设备名"对话框，写新 IP/掩码/网关，**重新上电后生效**【编程手册 p51-52】
- 【手册】H5U 出厂默认 IP 为 192.168.1.88，可经面板 MFK 键恢复默认；**该恢复方法仅针对 H5U（面板数码管+MFK），Easy521 的出厂默认 IP 与恢复方法在本机两本手册中均未记载**【编程手册 p392-393】
- 【手册】运行中也可经系统变量改 IP：`_Ethernet.IPCommand`=1 → 写 `_Ethernet.IPAddress`（16 进制，如 C0A80158）→ IPCommand=2【编程手册 p393】；IP/掩码/网关均可读的系统变量为 `_Ethernet.IPAddress/_Mask/_Gateway`【编程手册 p135】
- 【手册】**AutoShop 网口编程/下载所用的应用层协议本机手册未公开**（只描述软件操作步骤）；以太网应用协议仅记载 Modbus TCP、FINS TCP/UDP（从站、9600 端口）、EtherNet/IP、OPC UA、socket 指令【编程手册 p392、p408】→ 编程协议为厂商私有，第三方无法直接复刻【推断】

### A3. Modbus TCP 从站（服务器）——WSL 侧最相关的部分
- 【手册】"Modbus TCP 从站即 Modbus TCP 服务器，**默认开启 Modbus TCP 服务**，无需设置通信协议，**端口号 502**；最多支持 31 个客户端同时连接"【编程手册 p401】
- 【手册】从站支持功能码：0x01、0x02(同0x01)、0x03、0x04(同0x03)、0x05、0x06、0x0F、0x10，及 0x80-0xFF 标准错误码【编程手册 p401】
- 【手册】**寄存器映射（FC03/FC04/FC06/FC16 可访问区）**：

  | 软元件 | 数量 | Modbus 地址（十进制） |
  |---|---|---|
  | D0–D7999 | 8000 | **0–7999（即 FC03 地址 = D 编号，1:1 直映）** |
  | R0–R32767 | 32768 | 12288–45055 (0x3000-0xAFFF) |

  **不支持 W 元件、不支持 Pointer 变量**【编程手册 p402】；串口 Modbus RTU/ASC 从站映射表完全相同【编程手册 p386】
- 【手册】线圈映射（FC01/02/05/15）：M0-7999→0-7999；B0-32767→12288-45055；S0-4095→57344-61439；X0-X1777(8进制)→63488-64511；Y0-Y1777(8进制)→64512-65535【编程手册 p401】
- 【手册】站号：主站侧明确"**Modbus TCP 通信通过 IP 地址来确定从站，不对从站站号做检查**，从站站号可以使用默认地址，无需修改"【编程手册 p396】；从站侧存在只读系统变量 `_MbTcpSlv.SlaveAddress/_Port/_Connected/_Connections/_IPAddress[32]`【编程手册 p136】。TCP 从站站号默认值手册未给出数值。
- 【手册】Modbus TCP 主站（客户端）：最多 31 从站；从站端口建议缺省 502；超时 100-10000ms 缺省 500ms；重发 0-15 次缺省 1；触发方式"循环/触发"；映射地址支持自定义变量；功能码同上，单帧上限 FC03/04 读 125 字、FC16 写 123 字【编程手册 p394-397】

### A4. 字节序/字序
- 【手册】附录 26.1：Modbus"使用 big-Endian 表示地址和数据项"，16 位寄存器 0x1234 先发 0x12 再发 0x34——即**寄存器内字节序为大端**【编程手册 p952】
- 【手册】**32 位浮点跨两个寄存器的字序（ABCD/BACD/CDAB 等）在两本手册中均无记载**；手册全文无"字节序/字序/SWAP/大端浮点"的浮点映射说明（全文检索为空）
- 【档案】本项目契约自定：每个 REAL32 量占 2 个保持寄存器，**大端 ABCD**（1.0 → 3F 80 00 00），输入 0-13、输出 14-17，见 `plc/easy521/modbus_register_map_real32.csv`；README 要求联调时用 1.0/-1.0/0.95 验证字序【plc/easy521/README.md L32-36】。该契约目前只经 Python 镜像回归验证（9/9），**未在真机或模拟器上做过浮点读回验证**【档案，VALIDATION_REPORT.md】

---

## B. USB 口（Type-C）

- 【手册】部件：**Type-C 接口，"与 PC 通信"**【Easy手册 p102-103 部件说明④】；规格表："Type-C：支持用户程序上下载和固件升级（或通过 GE20-TF 存储扩展卡…）"【Easy手册 p104】
- 【手册】AutoShop USB 连接：通信类型选"USB"→单击"测试"；Easy 系列"RUN 指示灯闪烁"表示已连接成功；连上后即可下载/上载/监控/在线修改，也可经 USB 修改 PLC IP【编程手册 p52-54、p47】
- 【手册+图】USB 枚举：**Win10 识别为 "USB Serial Device (COM4)"（端口 COM 和 LPT 之下）**——即 USB CDC 虚拟串口类；**Win7 识别为 "H5U USB Device"** 需手动装驱动，驱动无数字签名（向导中"这个驱动程序没有经过数字签名！"），型号名 H5U USB Device【H5U USB驱动安装操作说明 p1-6 截图】
- 【手册】USB 编程/下载的私有协议本机资料未公开；另有 USB 固件烧录流程（26.2.1）【编程手册 p961】
- 【档案】实操注意："没有把 Windows 的普通串口枚举成功当作 PLC 编程通信成功；以 AutoShop 的连接测试为准"；真机经 USB 查询返回 Easy521 / 6.11.0.1【HARDWARE_COMMISSIONING_REPORT.md §2.1】；入门教程同警示：不要仅凭出现 COM 口判定已连接【AutoShop_Easy521入门教程.md L130】
- 【推断】驱动按文档是 Win7 时代 INF（未签名），Win10/11 用系统 usbser（CDC）即可枚举成 COM 口；但 **AutoShop 的"USB 通信类型"是否直接使用该 COM 口未记载**——WSL 里拿到 ttyACM/COM 设备不等于能复刻编程协议。

---

## C. 下载与在线监控的物理通道

- 【手册】"PLC 与 AutoShop 所在 PC 支持通过 **USB 或以太网**建立通信连接，实现**程序上下载、监控和调试**"【编程手册 p45】
- 【手册】Easy521 规格：以太网、Type-C 两条通道均支持"用户程序上下载和固件升级"；另有 SD 卡（GE20-TF，FAT32，PLCProgram 目录，上电自动烧录）与 Down/Updown 文件通道【Easy手册 p104、p121-122】【编程手册 p971-983】
- 【手册】在线监控走同一条 AutoShop 连接（USB 或以太网）；另有 Trace 监控、监控表、HMI 监控（H5U TCP 监控协议，127.0.0.1 用于无实物调试）【编程手册 p63、p75-80、p807-808】
- 【手册】**STOP 模式："PLC 停止程序的扫描及 Y 点的输出，通信功能停止运行"**【编程手册 p64】——用户程序发起的通信（含 Modbus TCP 主/从站扫描逻辑）随 STOP 停；而 AutoShop 编程链路本身在 STOP 下仍可用（档案实证：STOP 状态下 USB 监控、设备信息查询、故障诊断都正常）【档案，HARDWARE_COMMISSIONING_REPORT.md §8.4-8.5】
- 【手册】下载设置项："下载工程源码"（不勾则不能上载回读）、保持型变量属性（保持/重新初始化）；若设了登录密码，上载/下载/在线修改/程序校验/**修改 IP** 均需先登录【编程手册 p62-63、p70】
- 【档案】真机实绩：USB 完整工程下载成功（15:15:34）→ 厂家"程序校验"一致（15:16:14）→ 三次软件 RUN 失败；未走网口下载过【HARDWARE_COMMISSIONING_REPORT.md §8；evidence/hardware_usb_stage_20260928.json】

---

## D. RUN/STOP：面板开关与软件 RUN 的关系

- 【手册】"PLC 有 RUN 和 STOP 两种工作模式……切换工作模式有以下两种方法，**选择其中一种即可**：①拨动 PLC 设备上的物理开关 RUN/STOP；②在工具栏单击运行/停止按钮"【编程手册 p64】——手册**没有写明两者冲突时（开关在 STOP 位、软件发 RUN）的优先级或报错行为**
- 【手册】Easy521 面板：**拨码开关 RUN/STOP"控制主机运行/停止"**；指示灯：RUN 常亮=用户程序运行、熄灭=停止；ERR 闪烁=严重错误；ETH1/ETH2 常亮=链路建立、闪烁=通信中【Easy手册 p102-103】
- 【手册】官方开机步骤："在 PLC 处于 STOP 状态下进行程序写入后，需运行系统时：1. 将系统设为 RUN 运行状态。2. **确认 RUN 指示灯常亮**，为黄绿色。3. 需停止运行时，将系统设为 STOP 状态，**亦可通过上位机后台运行停止**"【Easy手册 p121 5.6.1】——表述上"设为 RUN"靠开关，停止可由上位机做；未提软件 RUN 能否越过 STOP 位开关
- 【档案】3 次软件 RUN（15:19:15、15:22:50、16:13:26）均返回"未能正确切换至运行状态"，USB 监控始终 STOP，故障诊断总数 0；档案结论："**面板开关原因仍是待核查推断，不是已经证实的故障根因**。需要用户确认物理 RUN/STOP 开关位置和 RUN/ERR 指示灯"【HARDWARE_COMMISSIONING_REPORT.md §8.4、§9；hardware_usb_stage_20260928.json "external_action_needed"】
- 【推断】"下载完成对话框会把 PLC 切到运行状态"（编程手册 p63）与本次失败并存，与"开关在 STOP 位导致软件 RUN 被拒"的假设一致；这是 WSL 之外也待现场确认的第一检查项：看面板拨码位置 + RUN/ERR 灯。

---

## E. 现有 Windows 侧自动化资产（两个 .ps1）

### E1. `plc/tools/read_autoshop_sim_results.ps1`（约 76 行）
- 能做【档案】：
  1. UIAutomation 校验 AutoShop 打开的主窗口确为 `BESS_E521_TEST` 工程，且界面存在"离线调试"标记（不在模拟器模式就拒发请求）；
  2. `Get-NetTCPConnection -LocalPort 502` 确认 502 监听进程为 `...\AutoShop\Simulator\h5u_daemon.exe`；
  3. 原生 .NET TcpClient 发 Modbus TCP **FC03**（unit id=1）读 D500-D523（汇总）与 D601-D628（逐组结果），MBAP 事务配对与长度校验，输出 JSON（含时间、目的、模拟器路径/PID、工程名、判定 accepted）。
- 不能做【档案】：只读——脚本注释"writes are deliberately unsupported"；无写功能码；只认 127.0.0.1:502 回环；**对 USB 真机无任何作用**（真机在线监控走 AutoShop GUI，不是 Modbus）；也不会替你启动/加载模拟器。
- 该脚本内容即问题 G 的配置实证（见下）。

### E2. `plc/tools/autoshop_ui.ps1`（约 276 行）
- 能做【档案】：通用 AutoShop（32 位 MFC 主窗口 `Afx:`+`#32770`）GUI 驱动与探查工具集：inspect（控件树枚举）、screenshot/desktop（PrintWindow/屏幕截图）、tree（跨进程读 SysTreeView32 节点文本，含 32 位 TVITEM 布局处理）、keys/click/paste（前台激活+SendKeys/mouse_event/剪贴板）、background（向指定句柄 PostMessage 鼠标消息）、text（WM_SETTEXT）、file（识别"另存为/打开/导入/导出"对话框并填路径点按钮）、accept（匹配预期成功文本的弹窗自动点"确定"）、menus（递归枚举菜单及命令 ID）、log（读编译输出 ListBox，**owner-drawn 时读不出，须退回截图**）、diagnostics/maximize。
- 不能做【档案】：没有任何"编译/下载"的直连 API——编译、下载、RUN 都只能靠它驱动 GUI 间接完成；档案明言"**GUI 脚本不应作为无人监督的硬件部署脚本**"【HARDWARE_COMMISSIONING_REPORT.md §7】。
- 【推断】对 WSL 的意义：AutoShop 无 CLI/无头编译接口的公开证据为零，编译/下载仍要 Windows 侧 AutoShop GUI（人 + 该脚本辅助，或 RDP/计划任务方案）。

### E3. 其他可复用资产
- 【档案】离线回归（WSL 内可跑）：`python3 plc/easy521/tests/run_easy521_validation.py`（9/9）、`python3 plc/tests/run_full_validation.py`（16/16）、`build_vendor_acceptance.py --check`（仅静态预检）【README.md L61-71】
- 【档案】证据链：`evidence/vendor_sim_positive_run1/negative/positive_run2.json`、`hardware_usb_stage_20260928.json`、全套截图 PNG【plc/easy521/evidence/】

---

## F. PLC 的 IP / 网络配置记录

- 【档案】**未找到**。对 `plc/easy521/autoshop/BESS_E521_DEV/`、`BESS_E521_TEST/` 全部文件做 IPv4 正则的二进制 grep，零命中（`.hcp`、`TcpModbusConfig.cfg`、`PortConfig.cfg`、`_SYS_ETHERNET.svt` 均为厂商私有二进制/混淆格式，不可读）。
- 【档案】`hardware_usb_stage_20260928.json` 只记录 `transport: USB`，无 IP；三份报告/README 也没有真机 IP、子网或网口的任何记录；`field_communication_commissioned: false`。
- 【档案】`modbus_realtime.py`（原上位机工具）UI 默认值：IP `127.0.0.1`、端口 `502`、从站号 `1`；`pymodbus ModbusTcpClient`，读 FC03 地址 0-27（28 个寄存器、14 个 REAL64 输入），写 FC16 到地址 28-35（Pcmd/Qcmd 各 4 寄存器 double）——这是旧 64 位协议，且默认目标只是回环【modbus_realtime.py L424-460、L607-623】。**不能从中得出真机 IP**。
- 【推断】真机从未走网口联调（报告："实际网络…联调尚未执行"）。首次网口联调需：网线连接 → AutoShop"搜索"或 H5U 式默认 IP 试探 → 或经 USB 在通信设置里读/改 IP（USB 连接后可修改 IP/设备名，编程手册 p52-54）。Easy521 出厂默认 IP 手册未记载（对照：H5U 为 192.168.1.88）。

---

## G. 厂商离线模拟器的 Modbus TCP 服务

- 【手册】AutoShop V4.2.0.0+ 支持"离线调试"（工具栏启动离线调试器，运行当前工程）；离线调试**支持"以太网 ModbusTCP 协议"、以太网 TCP/UDP、Modbus RTU/ASC 主从（映射到 PC 的 COM9/16/17）、EtherNet/IP、OPC UA**，并可与 InoTouchPad HMI 在线仿真（"H5U TCP 监控协议"，IP 填 127.0.0.1）【编程手册 p802-803、p807-810】
- 【手册】Windows 模拟器局限（明说）："因 Windows 非实时系统原因，离线调试状态下定时器与实际 PLC 有一定差异"；"离线调试状态下，CPU 和内存占有率显示的是 PC 机的"【编程手册 p803】
- 【档案】实测服务配置（read_autoshop_sim_results.ps1 + vendor_sim_positive_run1.json）：
  - 监听者：`E:\Software\AutoShop\Simulator\h5u_daemon.exe`（AutoShop V4.11.0.5 附带），**127.0.0.1:502**（标准 Modbus TCP/MBAP）；
  - 请求：unit id（站号）= **1**，功能码 **FC03**；**FC03 偏移 = D 编号直接映射（offset 500 → D500）**，与真机从站映射规则一致；
  - 已验证可读区：D500-D523（验收汇总：passed/failed/total/first_failed/magic=521/扫描数）、D601-D628（逐组结果）；D523 为工程修订号（TEST=1，BENCH=3）；
  - 只读用法，从未写入；每轮模拟器 RUN 约 15-16ms 实际扫描（非 5ms），模拟器版本显示 6.11.0.0（真机 6.11.0.1）。
- 【推断】WSL2（NAT 模式）默认访问不到 Windows 的 127.0.0.1:502；需 Windows 侧端口转发、镜像网络模式、或在 Windows 侧跑读脚本（现有 .ps1 即此形态）。此条为本机环境常识，非手册内容。

---

## 附：档案页码引用偏差备忘

- 【档案】验收报告引用"编程手册第 67、210、731、855 页"（扫描周期、LiteST 版本要求、模拟器非实时、下载）【HARDWARE_COMMISSIONING_REPORT.md §3、§7】。本次核对实际页码：LiteST 版本要求（Easy 固件 ≥5.67.0.0、AutoShop ≥4.8.1.0、单文件 ≤1000 行）在 **p227**；扫描周期超时按实际时间在 **p66**；Windows 非实时声明在 **p803**。与档案引用存在数十至七十页的系统性偏移（疑为档案写作时引用了另一版 PDF 或笔记页码）。**引用本文档页码时以本文件为准。**
- 本机另有英文版《Programming and Application Guide.PDF》《Instruction Guide.PDF》与《H5U系列可编程逻辑控制器用户手册.pdf》《指令手册.PDF》未逐页核对（内容应与中文版对应）。
