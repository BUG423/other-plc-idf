# py2plc · WSL 直连汇川 Easy521 可行性结论与路线图（汇总）

更新：2026-10-16。本文是 `docs/wsl-feasibility/` 的总纲，事实依据见 [01-protocol-facts](01-protocol-facts.md)、[02-opensource-ecosystem](02-opensource-ecosystem.md)、[03-wsl-probe-results](03-wsl-probe-results.md) 与 `wsl-probe/usb-rtu/` 原始数据。

## 一、问题

能否在 WSL（Linux）内完成对汇川 Easy521-0808TN 的 **编程 → 烧录(下载) → 反馈监测** 闭环？约束：近期无网口（拓展坞下半月到位），仅 USB Type-C 单线。

## 二、结论速览

| 能力链 | 纯 WSL 原生 | 桥式（WSL 编排 + Windows 执行） | 依据 |
|---|---|---|---|
| 反馈监测（网口） | ✅ 5/5，Modbus TCP 默认开启、D0-D7999 与 FC03 地址直映 | — | 手册 + EMQX Neuron + HslCommunication 三方互证 |
| 反馈监测（USB 单线） | ❌ RTU 证伪 | ⚠️ GUI 监控截图回读可用；原生协议逆向待抓包评估 | 15 组合实测静默（`wsl-probe/usb-rtu/`） |
| 编程（编译） | ❌ AutoShop 无 CLI/无头接口；Wine 零成功记录 | ✅ autoshop_ui.ps1 GUI 驱动；中期线索 autoshop-mcp（H3U 已证 DLL 无头编译可行，H5U/ST 需适配） | 02 文档 |
| 烧录（下载） | ❌ 私有协议全网零文档零逆向；编译产物格式私有 | ✅ USB 下载由 AI 驱动 AutoShop 完成（已在真机人工验证过该通道） | 01/02 文档 |
| USB 传输层 | ✅ usbipd 挂载打通，内核 cdc_acm 认领 /dev/ttyACM0 | — | 2026-10-16 实测 |

**核心判断**：USB 单线约束下，"AI 直接上传编译代码 + 拿回反馈"以 **桥式形态** 完全可达；纯原生下载/监测取决于协议逆向立项（拓扑利好，见下）。

## 三、关键事实（新获取）

1. **USB 设备自述**：`Easy52x USB Device v2.4`，`Linux 3.10.65 with sunxi_usb_udc` → 主控为全志（Allwinner）sunxi 平台，**PLC 内部运行嵌入式 Linux**。
2. **纯 CDC 拓扑**：控制接口（class 02/02/01，中断 EP 0x84）+ 数据接口（class 0A，bulk EP 0x02/0x81，512B），无厂商专有接口。AutoShop 的 libusb0 驱动直驱这对标准 bulk 端点 → **WSL 侧 pyusb 可达相同通道，缺的只是协议内容**。
3. **Modbus TCP 从站**：出厂默认开启，端口 502，不校验站号，D0-D7999 ↔ FC03 地址 0-7999 直映；**32 位浮点跨寄存器字序官方零记载**（EMQX Neuron 默认 CDAB，与项目 ABCD 契约可能冲突）→ 真机首连必须 `--calibrate`。
4. **真机 IP 无任何记录**（工程文件厂商二进制混淆格式）；网口路径首步 = AutoShop 搜索或插网线后扫描。
5. **环境雷**：①代理 TUN 开启时所有 TCP"假连接"，联调数据不可信；②Hyper-V 防火墙默认阻断 WSL→Windows（模拟器桥需放行规则）；③USB 挂载与 AutoShop 使用互斥（attach/detach 切换）。
6. **RUN/STOP**：面板拨码与软件按钮二选一；此前 3 次 RUN 失败与"拨码在 STOP 位"假设一致（用户已拨 RUN，待读回确认）。

## 四、已建成资产

| 资产 | 位置 | 说明 |
|---|---|---|
| 虚拟 Easy521（TCP 从站，三场景） | `wsl-tools/virtual_easy521.py` | steady / low_voltage / overload，D18=模式扩展 |
| 只读监测客户端 | `wsl-tools/e521_monitor.py` | TCP + 串口(RS485) 双传输，`--calibrate` 字序判别，表格/JSONL |
| 寄存器契约唯一定义 | `wsl-tools/e521_regs.py` | 9 量偏移、REAL32 ABCD/BADC 编解码 |
| 测试套件 | `wsl-tools/tests/` | **65 项全过**（2026-10-16 独立复验） |
| USB 探测证据 | `wsl-probe/usb-rtu/` | RTU 证伪原始数据 + USB 端点拓扑 |
| AutoShop GUI 驱动工具集（已有） | `plc/tools/autoshop_ui.ps1` | 截图/控件树/点击/弹窗确认，待接通编排 |
| 侦察文档 | `docs/wsl-feasibility/01-03` | 手册事实 / 开源生态 / 环境实测 |

## 五、路线图

- **P1 侦察与软件闭环**（✅ 完成）：三路侦察 + 虚拟 PLC + 监测工具 + 字序校准。
- **P2 GUI 桥接演练**（进行中，待用户在场）：设备 detach 归还 Windows → AutoShop 打开 DEV 工程 → 只读监控截图回读演练 → （用户授权后）编译+USB 下载全流程。全程 USB 单线。
- **P3 协议抓包评估**（待用户决定）：Windows 装 USBPcap（管理员一次）→ 抓一次 AutoShop 正常连接会话 → 帧结构分析 → 再决定是否立项原生 USB 协议栈（下载器/监测器）。
- **P4 网口直连**（待拓展坞，下半月）：插网线 → 绕开 TUN（关闭或加直连规则）→ 发现 IP → `e521_monitor.py --host <IP> --port 502 --calibrate` 真机只读监测；顺带验证 RUN 状态（确认拨码根因）。
- **长线观察**：autoshop-mcp 的 H5U/ST profile 动向；若立项原生 RE，从 bulk 端点帧结构入手。

## 六、红线（不变）

对真机只读（FC01/02/03/04）；写操作/RUN/STOP/下载仅在用户明确授权且在场时执行；下载前确认工程目标与备份；扫描/联调前确认 TUN 已绕开。
