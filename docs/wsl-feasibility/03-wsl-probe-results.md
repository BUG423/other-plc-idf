# 03 WSL 环境实测报告（只读探测）

- 日期：2026-10-16
- 执行环境：WSL2 Ubuntu（NAT 模式），eth0 = 172.18.107.122/20，网关 172.18.96.1（Windows 主机）
- 探测脚本与原始数据：`/home/zss/workspace/py2plc/wsl-probe/`
- 安全红线执行情况：全程只读（仅 FC01/02/03 探测能力验证，未对任何外部设备发过任何 Modbus 报文）；扫描仅 TCP connect、仅 10.185.16.0/24、仅指定 9 端口、0.4s 超时；未做指纹识别；未执行 usbipd bind/attach；未安装系统包（pymodbus 3.16.1 装在 `.venv`）；Windows 侧仅起过一个临时 TcpListener（已杀、临时文件已删），未改动任何文件。

---

## ① WSL → Windows 主机通路测试结论

**结论：不通，被 Windows 防火墙静默丢弃（DROP）——这是本次实测最重要的负面发现。**

| 目标 | 结果 |
|---|---|
| 172.18.96.1:5020（网关 = Windows vEthernet） | connect 超时（被过滤丢弃） |
| 172.18.96.1:5021（关闭端口基线） | 同样超时而非 RST 拒绝 → 包根本没到监听栈 |
| 10.185.16.82:5020（Windows WLAN 物理网卡） | 超时 |
| Windows 本机回环 127.0.0.1:5020（Test-NetConnection） | **成功**（监听器本身工作正常） |
| 监听器日志 | WSL 发起的连接数为 **0**（SYN 未到达应用层） |

防火墙状态（只读查询）：
- `Get-NetFirewallProfile`：Domain / Private / Public 三个配置文件全部 `Enabled=True`
- `Get-NetFirewallHyperVVMSetting`（WSL 的 Hyper-V 防火墙）：`Enabled=True`，`DefaultInboundAction=Block`

即：WSL 出网（NAT 转发）没问题（能连公网 223.5.5.5:443，3ms），但**任何从 WSL 主动连 Windows 主机端口的入站流量默认被 Hyper-V/Defender 防火墙丢弃**。将来若把厂商 Modbus 模拟器放在 Windows 上让 WSL 连，必须先在 Windows 加放行规则（需管理员操作，本次未做）。

### 额外发现：代理 TUN 会伪造 TCP 连接（假阳性）

Windows 上有代理 TUN 网卡 `Meta`（198.18.0.1，路由 `0.0.0.0/0 via 198.18.0.2`，RouteMetric 0）。实测中它在 13:04–13:06 之间开始对**所有** TCP connect 立即回 SYN-ACK（fake-accept）：
- 证据：保留地址 TEST-NET-3 `203.0.113.7:502` 和空的 `10.255.255.1:502` 都在 0–4ms 内"连接成功"（物理上不可能）；13:09 复测仍如此。
- 影响：**TUN 开启期间，WSL 对局域网/外网的 TCP 探测结果全部不可信**。第二次扫描因此作废（原始数据保留在 `plc_discover_run2_INVALID_tun_fakeaccept.json`）。
- 对项目的意义：以后从 WSL 直连 PLC 时，若 TUN 处于开启状态，轻则连接被劫持、重则读到假数据——**联调时必须把 PLC 网段加入代理直连/绕过名单，或关闭 TUN 模式**。

## ② PLC 在线状态 / 端口 / 寄存器

**结论：以太网侧不在线——10.185.16.0/24 上没有 PLC；但 PLC 本体在线（USB 可见，见 ③），只是没插网线/未接入本网段。**

- 第一次扫描（TUN 截获发生前，结果可信）：254 主机 × 9 端口 = 2274 次 TCP connect，**0 开放**。
- Windows ARP 旁证（`Get-NetNeighbor`）：WLAN 网段仅 `10.185.16.100`（随机化 MAC `D6-17-35-AB-9E-9F`，实际是当前上网网关）可达，该设备 9 个端口也全无响应；无任何 502 开放设备。
- 协调方确认：PLC 目前只连 USB、无网线 → 扫不到是预期结果，非故障。
- 因未发现 502 开放设备，**未对任何真实设备执行 Modbus 读操作**（遵守"疑似 PLC 才探测"的步骤约束，也没有任何真实 IP 可读）。

**Modbus 工具链自测（127.0.0.1 本机从站，不涉及外部设备）——通过：**

用手写的标准 Modbus TCP 从站（FC01/02/03）+ pymodbus 3.16.1 客户端验证将来要用的代码路径（`modbus_selftest.py`）：
- 站号 1，FC03 读 0~31：OK；FC01 读线圈 0~15、FC02 读离散量 0~15：OK
- REAL32 解码验证：按契约预置 9 个量（220.50 / 0.9871 / 0.0213 / 0.4321 / 0.7654 / -0.1250 / 0.0099 / 0.8500 / -0.3000），**大端 ABCD（reg0=高字）解码全部精确还原**；字交换 BADC 解码为乱码（如 -2.4e-41），可直接用作"字序判错"的判据。原始寄存器与两种解码全量数据见 `modbus_selftest.json`。
- pymodbus 3.16.1 API 注意点（踩坑记录）：旧式 `slave=` 关键字已更名 `device_id=`；server 侧 datastore API 大改（`ModbusDeviceContext`/`devices=`），本自测因此用手写从站，不影响客户端用法。

## ③ USB 现状

| 项 | 结果 |
|---|---|
| Windows 设备列表 | **`H5U USB Device`，Status=OK，类=PlcUsb，`USB\VID_0471&PID_A531\7&83714DF&0&4` —— 即 Easy521 本体在线**（与协调方"PWR 灯亮、RUN 档"一致；也与 2026-09-28 AutoShop 经 USB 全工程下载成功的记录吻合） |
| COM 口映射 | **无**。Windows 注册表 `SERIALCOMM` 键不存在（系统零 COM 口）；H5U 走厂商自定义 "PlcUsb" 类驱动而非 USB-CDC，**不会枚举出 COMx，因此这条 USB 线路不能直接跑 Modbus RTU** |
| WSL 侧 | `/dev/ttyACM*`、`/dev/ttyUSB*` 均无；`/dev/bus/usb` 整个目录不存在——WSL2 当前没有任何 USB 透传 |
| usbipd | Windows 上**未安装**（`where usbipd` 找不到），`usbipd list` 无法执行；按红线未做 bind/attach。另有一台 `Error` 状态的 `VID_1f3a_PID_efe8` 设备，与 PLC 无关 |

## ④ 对后续方案的影响

1. **数据链路首选：给 PLC 插网线，走 Modbus TCP（502）**。这是唯一自然符合项目契约（FC03 0~17 REAL32）的通路。接线后需确认 PLC 的 IP（档案无记录；建议用 AutoShop 在线查看或厂商默认网段，注意默认 IP 很可能不在 10.185.16.0/24，届时 PC/WSL 需加同网段地址）。
2. **WSL 直连 PLC 出网方向目前可行但有代理隐患**：NAT 出站本身通（公网 3ms），但 Meta TUN 一旦开启就会劫持/伪造连接。联调前必须：把 PLC 网段加入代理绕过名单或关 TUN；建议在客户端代码里加防假连接校验（如读两次固定的"签名字寄存器"，或对解码值做量程合理性检查——BADC 乱码特征也可利用）。
3. **"模拟器放 Windows"方案要先解决防火墙**：Hyper-V 防火墙默认 Block WSL→主机入站。要么加放行规则（管理员，一次性），要么把模拟器直接放进 WSL（推荐——零配置，本自测已证明 WSL 里跑 Modbus 从站 + pymodbus 客户端链路全通）。
4. **USB 方案成本高，仅作备选**：无 COM 口（不能 pymodbus serial 直连）；要走 usbipd 需先在 Windows 安装 usbipd 并 bind/attach（红线禁止本次执行，且 "PlcUsb" 是厂商自定义类，Linux 侧未必有驱动）。除非以太网彻底不可行，不建议投入。
5. 本次未覆盖：PLC 真实 IP 的确认、网线接入后的完整 FC03 实测（含 REAL32 字序确认）。建议网线接好后按 `wsl-probe/modbus_selftest.py` 的客户端逻辑直接复用。

---

### 附件清单（`/home/zss/workspace/py2plc/wsl-probe/`）

| 文件 | 内容 |
|---|---|
| `tcp_path_test.py` / `tcp_path_test.json` | WSL→Windows 三目标 TCP 通路测试脚本与结果 |
| `plc_discover.py` | 只读 TCP connect 扫描器（/24 × 9 端口 × 0.4s） |
| `scan_summary.json` | 两次扫描的方法、结果与有效性判定 |
| `plc_discover_run2_INVALID_tun_fakeaccept.json` | 作废的第二次扫描原始数据（TUN 假阳性证据） |
| `modbus_selftest.py` / `modbus_selftest.json` | 本机 Modbus 自测（手写从站 + pymodbus 客户端 + ABCD/BADC 解码验证） |
| `usb_inventory.json` | USB 现状（H5U VID/PID、无 COM 口、WSL 无透传、usbipd 缺失） |

环境版本：Python 3.12.3，pymodbus 3.16.1（venv：`/home/zss/workspace/py2plc/.venv`）
