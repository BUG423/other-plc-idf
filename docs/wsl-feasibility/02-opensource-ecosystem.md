# WSL 可行性侦察 · 02 开源生态与联网调研

- 调查日期：2026-10-16。方法：联网检索（搜索引擎 + GitHub API 逐仓核实 + 官方 PDF 下载）+ 与 `01-protocol-facts.md`（本机官方手册硬事实）交叉验证。
- 目标设备：汇川 Inovance Easy521-0808TN（H5U&Easy 系列，固件 6.11.0.1），AutoShop V4.11.0.5。
- 可信度标注：**[官方]**=厂商文档/官网；**[高]**=多个独立第三方交叉印证且与官方行为一致；**[中]**=单一第三方来源；**[低]**=论坛/博客传言未验证；**[未证实]**=本次调研未找到任何公开来源。
- 与 01 文档的分工：01 回答"官方手册说了什么"，本文回答"开源社区/互联网有什么可用的"。

---

## ① 协议面事实表（联网调研侧）

### 1.1 三条以太网/USB 通道总览

| 通道 | 端口/形态 | 用途 | 公开程度 | 联网证据可信度 |
|---|---|---|---|---|
| Modbus TCP 从站 | TCP **502**，出厂默认开启，最多 31 客户端 | 数据读写（监测链） | 完全公开（标准协议 + 汇川地址映射） | [官方]（01 手册 p401）+ [高] 多方印证 |
| AutoShop 私有编程/下载/监控协议 | 端口号**未公开**（见 1.4） | 编译产物下载、上载、在线监控 | **零公开**：无文档、无逆向 repo、无端口记录 | [官方]（01 手册明确仅记载软件操作步骤）+ 本次联网 0 逆向记录 |
| USB Type-C 编程口 | Win10 枚举为 **USB CDC 虚拟串口**（"USB Serial Device (COM4)"）；Win7 需装未签名驱动"H5U USB Device" | 下载/上载/固件升级/监控 | 枚举类型已证实（官方驱动安装说明截图）；**编程协议零逆向**，VID/PID 无公开记录 | [官方]（01 文档 B 节引 USB 驱动安装说明 p1-6）+ 联网 0 逆向记录 |
| EtherNet/IP（EIP） | TCP/UDP 44818 标准端口（社区教程走 EDS 导入组态） | PLC 间/主从数据交换，非编程口 | 有公开 EDS 与教程，但与编译下载无关 | [中]（社区教程） |

关键交叉验证结论：**EMQX Neuron 与 HslCommunication 两家独立第三方给出的 H5U/Easy 软元件→Modbus 地址映射表，与 01 文档从官方编程手册摘录的 p401/p402 映射表逐项一致**（D 0-7999、R 12288-45055、M 0-7999、B 12288-45055、S 57344-61439、X 63488-64511、Y 64512-65535），三方互证，可放心作为 WSL 监测链的寻址依据。

### 1.2 Modbus TCP 细节（WSL 监测链直接可用）

| 项 | 事实 | 来源 |
|---|---|---|
| 服务端口 | 502，从站默认开启，无需配置 | [官方] 编程手册 p401（01 文档）；[高] CSDN Easy 系列以太网通讯教程同述 |
| 支持功能码 | 0x01/0x02/0x03/0x04/0x05/0x06/0x0F/0x10 | [官方] 编程手册 p401 |
| 字/位区映射 | D0-D7999→Modbus 0-7999（1:1 直映）；M/B/S/X/Y 见 1.1 表 | [官方]+[高] 三方一致 |
| 寄存器内字节序 | big-endian（0x1234 先发 0x12） | [官方] 编程手册附录 26.1 p952 |
| 32 位浮点跨寄存器字序 | **官方手册未记载**（ABCD/CDAB 均无说明）；Neuron 驱动默认 float=#LL（字交换，CDAB 型） | [官方·未记载] + [中] Neuron 默认值 → 必须真机实测（本项目契约 REAL32 大端 ABCD，见 01 A4 节验证要求） |
| TCP 从站站号 | 主站侧按 IP 寻址、不检查站号；本厂模拟器用 unit id=1；Mech-Mind 文档示例从站地址 255 | [官方] p396 + [中] Mech-Mind（255 可能为其特定组态，以实测为准） |
| 单帧上限 | FC03/04 读 125 字，FC16 写 123 字 | [官方] p394-397 |
| 对本项目 0-17 号寄存器 | REAL32×9 对应 D 区 18 字，地址 0-17 直发，502 直连 | [官方] 推论，无争议 |

### 1.3 "20256/20257 端口"核查结论

**[未证实]**。中英文多轮检索（含精确短语、site 限定、GitHub 代码检索）均未找到任何把 20256/20257 与汇川 PLC/AutoShop 关联的公开记录。01 文档也确认官方手册未记载编程口端口号。该记忆可能来自其他厂商/其他软件（未定位到出处）。**结论：不要把 20256/20257 写进任何方案假设**；真实端口用 `nmap -p-` + Wireshark 抓 AutoShop 下载过程即可确定（见 §3.6）。

### 1.4 AutoShop 私有协议逆向现状

**[未证实]**：GitHub 上不存在任何实现了"AutoShop 网口下载/在线监控协议"的开源 repo（检索 inovance/H5U/Easy521/autoshop 相关 35+ 仓库逐一核查，全部只做 Modbus 数据采集或 Windows GUI 自动化）。这意味着：WSL 内"自研下载器"没有现成轮子，从零逆向需要抓包分析私有二进制协议，工程量大且有变砖/合规风险。

### 1.5 USB 编程口的 Linux 视角补充

- 官方驱动安装说明（01 文档 B 节）证实 Win10 走系统 usbser（CDC 类）→ 依此推断 **Linux 下应枚举为 /dev/ttyACM0（cdc_acm 驱动）**，`usbipd-win` 可把它挂进 WSL [推断，未实测]。
- 但拿到 ttyACM ≠ 能编程：AutoShop over USB 用的应用层协议未公开、无逆向记录（与 1.4 同理）。01 文档的警示（"出现 COM 口 ≠ 编程通信成功"）同样适用于 Linux。
- VID/PID：无公开记录 [未证实]。拿到实物后 `lsusb`/设备管理器硬件 ID 一查即得（见 §3.7）。

---

## ② 开源项目清单与评级

评级维度：★=对"WSL 下完成编程/烧录/监测"的实际价值（1-5）；License；Linux 可用性。星数/时间截至 2026-10-16（GitHub API 实测）。

| # | 项目 | Stars | 语言 | License | 最近提交 | 功能一句话 | 评级 |
|---|---|---|---|---|---|---|---|
| 1 | apache/plc4x | ~1.1k(仓库) | Java | Apache-2.0 | 活跃 | 工业协议统一适配器（无 inovance 驱动） | 监测 3★ |
| 2 | emqx/neuron | 数千(仓库) | C | LGPL-3.0 | 活跃 | 工业协议网关，含"Inovance Modbus TCP"驱动 | 监测 3★/参考 5★ |
| 3 | dathlin/HslCommunication | 2135 | C# | 新版闭源/旧版 LGPL | 活跃 | .NET 工业通讯库，含 Inovance 全系驱动 | 参考 4★ |
| 4 | JonathanChan-geek/autoshop-mcp | 0 | Python | MIT | 2026-09-20 | **无 GUI 调用 AutoShop 原厂 DLL 编译 H3U 工程（CLI/MCP）** | 编程链 4★(H3U)/对 Easy521 暂 0★ |
| 5 | ChangYuan-.../inovance_tag_cyg | 5 | Python | 无 | 2025-11 | 包装汇川官方 Windows DLL 的标签通讯 | 1★（仅 Windows） |
| 6 | mipu1711/Protocol-Inovance | 5 | C# | 无 | 2025-04 | H5U/H3U/Easy/AM600 Modbus TCP 读写（依赖来路不明 DLL） | 2★ |
| 7 | lemonchen2021/ModModbusTcpH5U | 1 | C# | 无 | 2025-11 | "测试汇川 Easy521 读写成功" | 1★（旁证） |
| 8 | ChinaR0C/HcPlcApi | 0 | C++ | 无 | 2026-05 | 汇川 PLC Modbus 源码 | 1★ |
| 9 | clei32879-gif/electrical-copilot | 7 | Python | 无 | 2026-09 | AI 生成"可用 AutoShop 打开的工程文件" | 2★（待验证） |
| 10 | agentthink/plc-sweep | 16 | C# | 无 | 2026-09 | 离线 MAC 前缀识别厂商+端口扫描（Windows） | 1★ |
| 11 | pymodbus-dev/pymodbus | 2k+ | Python | BSD-3 | 活跃 | 通用 Modbus 库 | 监测 5★ |

### 2.1 Apache PLC4X —— 没有 Inovance 原生驱动（核实结论）

- 直接核实 `apache/plc4x` 仓库 `plc4j/drivers/` 目录（GitHub contents API），驱动全集为：ab-eth, ads, bacnet, c-bus, can, canopen, ctrlx, eip, firmata, iec-60870, knxnetip, mock, **modbus**, opcua, open-protocol, profinet-ng, profinet, s7, simulated, slmp, umas —— **无 inovance**。
- 仓库 issue/PR 全文检索 "inovance"：**0 条命中**。"PLC4X 贡献过汇川驱动"的记忆不成立（截至 0.13.x）。
- 连汇川 PLC 的官方姿势 = 用其 `modbus-tcp` 连接串自己填映射地址（社区通行做法）；也就是说 **PLC4X 对本项目不提供超出 pymodbus 的增值**，反而引入 JVM 重量级依赖。
- **plc4py（Python 实现）处于 sandbox/实验阶段**：官网文档将 plc4py 列为次要语言实现，Maven 坐标为 `org.apache.plc4x.sandbox:plc4py`（sandbox 命名空间），0.12 起主线重心是 MSpec 生成的 plc4j。不建议在其上压宝。
- 来源：https://github.com/apache/plc4x 、https://plc4x.apache.org/plc4x/0.12.0/users/protocols/modbus.html 、https://plc4x.apache.org （[高]，API 直查）

### 2.2 EMQX Neuron —— 最权威的第三方汇川地址映射 + 可选运行时

- 开源工业协议网关（C，LGPL-3.0），南向驱动列表含 **"Inovance Modbus TCP"**——本质是 Modbus TCP 实现 + 汇川点位映射预设（"适配汇川 PLC 点位"），并明确支持 Easy 系列。
- 其文档中的汇川地址映射表（见 §1.1 交叉验证）是全网最完整的第三方记录，与本机官方手册一致，可与 01 文档互为备份信源。
- Linux/WSL 可跑（官方 Docker 镜像），Web UI 管理；若不想自己写 pymodbus 脚本，它可当"开箱即用监测网关"。
- 来源：https://docs.emqx.com/zh/neuronex/latest/configuration/south-devices/modbus-hc-tcp/modbus-hc-tcp.html （[高]）

### 2.3 HslCommunication —— 代码闭源但地址文档公开的第二信源

- 主库 2135 星；Demo 源码里有 `HslCommunication.Profinet.Inovance` 命名空间的完整设备窗体：`InovanceTcpNet`（串口/TCP）、`InovanceConnectedCip`、以及 **`InovanceEasyNet`**（Easy/H5U 网口专用类，支持 `D100.0` 位读取、W/R/U 寄存器）。
- 但**类库新版本源码闭源收费**（仓库只放 Demo）；LGPL 的最后免费版（HslCommunication-Community，2019）不含 Inovance 类。跨平台要 .NET runtime + 商业授权，**对 WSL 方案无直接复用价值**。
- 保留价值：其公开的地址示例文档（`HslCommunicationDemo/PLC/Inovance/Helper.cs`）逐项给出 H5U/Easy 软元件范围（M0-M7999、B0-B32767、S0-S4095、X/Y 八进制 0-1777、D0-D7999、R0-R32767），与官方手册、Neuron 三方一致。
- 来源：https://github.com/dathlin/HslCommunication （raw 文件直读核实）（[高]）

### 2.4 autoshop-mcp —— 本调研最有价值的发现（无 GUI 编译的先例）

- **MIT 协议，2026-09 仍活跃**。核心能力：**不打开 AutoShop 主程序、不模拟鼠标键盘，通过独立 x86 原生进程调用本机 AutoShop 的编译/转换 DLL**，实现 H3U 工程的：读取/IL 修改（带哈希校验）/LD↔IL 转换/全局符号表读写/**原厂编译并打包**（`native_compile_copy` 一条命令出 ZIP + 机器码哈希），共 19 个工具，CLI（`--params-file` JSON）与 MCP 两种入口，明确面向 AI 编程工具接入。
- 有效性证明方式严谨：加载前校验 DLL SHA-256 指纹、编译结果二次一致性校验、LD→IL 前后机器码逐字节对比。
- **对本项目（Easy521）的限制（README 明说）**：
  1. 当前 profile 仅验证了一个 H3U 运行库组合（`h3u-4.10.2.4-local-1`），"暂不提供 ST、**H5U**、受保护工程的原生编译"——Easy521 属 H5U 架构且用 LiteST，**当前开箱不可用**；
  2. 需要 **Windows + 本机安装 AutoShop + VC90 MFC 运行库**（Wine 未验证、未声明支持；其加载链 MFC90/DLL 指纹校验在 Wine 下大概率失败 [推断]）；
  3. 明确"不提供连接 PLC、下载、运行或停止接口"——**不解决烧录问题**。
- 对我们的意义：**证明了"WSL 生成代码 → Windows 侧无 GUI 编译 → 得到可下载产物"路线成立**。H5U 适配 = 自行核对本机 AutoShop V4.11.0.5 的 DLL 指纹并扩 profile（工作量未知，但作者文档给出了完整的适配方法论与验证清单）。CI 已在 Linux 跑离线测试（读取/修改/打包部分跨平台）。
- 来源：https://github.com/JonathanChan-geek/autoshop-mcp （README 逐段核实）（[高]，限其声明范围）

### 2.5 inovance_tag_cyg —— Python 皮、Windows 芯

- PyPI 可装（`pip install inovance_tag_cyg`，要求 Python ≥3.7,<3.13），但仓库树显示其核心是随包分发的**汇川官方 Windows DLL**（InoTagAccess.dll、Tag.dll、RtosLib64.dll 等），走汇川 Tag 访问接口。**Linux/WSL 不可用**；且未经汇川授权再分发官方 DLL，合规性存疑。
- 来源：https://github.com/ChangYuan-Semiconductor-Equipment/inovance_tag_cyg （树形直查）（[高]）

### 2.6 其余小项目

- **mipu1711/Protocol-Inovance**（5★，C#，无 License）：README 自述走 Modbus TCP，但要往 bin 目录拷 `ModbusTcpAPI.dll`/`StandardModbusApi.dll` 两个二进制（来源不明）。仅作"H5U/Easy 可用 Modbus 读写"的旁证，不建议引用代码。
- **lemonchen2021/ModModbusTcpH5U**（1★）：自述"测试汇川 Easy521 读写成功"——Easy521 Modbus TCP 可行性的又一旁证（[低]）。
- **ChinaR0C/HcPlcApi**（0★，C++，无 License）：汇川 Modbus 源码，规模小、无文档。
- **clei32879-gif/electrical-copilot**（7★）：本地 AI 工作台，声称直出"可用 AutoShop 打开/粘贴的工程文件"（含汇川）。若其 AutoShop 工程生成器可用，可补"代码文本→.hcp 工程"一环；Windows 为主、无 License、生成质量未经验证（[中]）。
- **agentthink/plc-sweep**（16★）：MAC 前缀识别厂商（内置汇川前缀）+ 端口扫描（识别 S7:102 等），Windows 工具——找 Easy521 的 IP/开放端口时可用，但 nmap 在 WSL 内等价可办。
- **Orange-Cyberdefense/awesome-industrial-protocols**：泛工控协议资源列表，无汇川专节。
- 机器人/伺服类（InovanceRobotApi、inovance-servo-modbus-control 等）与本项目无关，略。

### 2.7 pymodbus —— 监测链首选库（版本建议）

- 当前最新稳定版 **3.16.1**（PyPI 实测），要求 **Python ≥ 3.10**；2.x 线终结于 2.5.3（legacy，勿用于新代码）；3.0 起 API 大改，网上大量旧教程（`ModbusClient('ip')` 同步老写法）已失效，照抄会踩坑。
- 建议：WSL 内直接 `pip install "pymodbus>=3.16,<4"`，用其同步 `ModbusTcpClient`；项目已有的"REAL32 大端 + 保持寄存器 0-17"契约与之完全兼容（struct 打包/解包自行控制字序，规避 1.2 的浮点字序未知问题——这正是自写优于网关预设的理由）。
- 来源：https://pypi.org/pypi/pymodbus/json （实测）、https://pymodbus.readthedocs.io （[高]）

---

## ③ 验证测试方案

前置说明（与 01 文档 G 节一致）：WSL2 默认 NAT 网络访问不到 Windows 的 127.0.0.1:502（模拟器）；真机走局域网 IP 则无此问题。模拟器验证需 Windows 侧转发或镜像网络模式。

### 3.1 真机 Modbus TCP 冒烟（监测链核心，WSL 内直接跑）

```bash
# 前置：网线连 Easy521，Windows 侧 AutoShop(USB) 确认/设置 PLC IP；WSL 能 ping 通
pip install "pymodbus>=3.16,<4"
python3 - <<'EOF'
from pymodbus.client import ModbusTcpClient
import struct
c = ModbusTcpClient("192.168.x.x", port=502)   # 换成实测 IP
c.connect()
rr = c.read_holding_registers(0, 18, slave=1)  # D0-D17
print(rr.registers)
# REAL32 大端 ABCD 契约解包（与 plc/easy521/modbus_register_map_real32.csv 一致）
vals = [struct.unpack('>f', struct.pack('>HH', *rr.registers[i:i+2]))[0] for i in (0,2,4)]
print(vals)
c.close()
EOF
```
预期：返回 18 个寄存器值；先在 AutoShop 监视表给 D0/D2 写入 1.0、-1.0，若解包得到 1.0/-1.0 则字序契约成立；得到乱值则试 CDAB（交换两个 HH 顺序）。**此实验同时终结 1.2 的浮点字序悬念。**

### 3.2 端口侦察（核实"私有编程端口"与 20256 传闻）

```bash
nmap -p- -T4 --open <PLC_IP>      # 预期至少 502 开放；其余端口即候选私有协议端口
```
再用 Wireshark（Windows 侧或 WSL `tcpdump -i eth0 -w`）抓 AutoShop 以太网"下载"全过程，`tshark -r dump.pcap -z conv,tcp` 看 AutoShop 与 PLC 的会话端口分布。产出：私有协议端口 + 首包样本（为未来逆向留档，同时直接证伪/证实 20256/20257）。

### 3.3 EMQX Neuron 网关验证（可选，不想写代码时）

```bash
docker run -d --name neuron -p 7001:7001 -p 1804:1804 emqx/neuron:latest
# 浏览器 http://<wsl-ip>:1804 → 南向设备 → 驱动选 Inovance Modbus TCP
# 填 PLC IP:502，点位加 D0 (uint16) / float 两寄存器组，开采集
```
预期：实时值页读出 D 区数据；float 值若异常则在其字节序配置里切换 #LL/#BB 重试——顺带反推真机浮点字序。

### 3.4 PLC4X plc4j 验证（仅当团队要 JVM 栈时）

```bash
mvn archetype:generate -DarchetypeGroupId=org.apache.plc4x -DarchetypeArtifactId=plc4j-archetype-client-sandbox ...
# 连接串： modbus:tcp://<PLC_IP>:502?unit-identifier=1
# 地址：   holding-register:0:REAL  （0.12 语法，D0 起）
```
预期：能读即证"PLC4X 走 Modbus 通汇川"；若预期 inovance 连接串（`inovance:tcp://...`）则必然失败——该驱动不存在（本调研已核实）。结论仍是：**除非已有 JVM 资产，否则选 pymodbus**。

### 3.5 autoshop-mcp 验证（Windows 侧，评估 H5U 适配成本）

```powershell
git clone https://github.com/JonathanChan-geek/autoshop-mcp.git; cd autoshop-mcp
py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e .
.\src\autoshop_mcp\native\build.cmd
$env:AUTOSHOP_INSTALL_DIR='C:\Program Files (x86)\AutoShop'   # 换成本机路径
.\.venv\Scripts\python.exe -m autoshop_mcp capabilities
```
- 看点 1：`native_compile.available` + `mismatches` 列表 → 得到本机 V4.11.0.5 与 profile `h3u-4.10.2.4-local-1` 的 DLL 差异清单；
- 看点 2：拿现成 Easy521 工程（`plc/easy521/autoshop/BESS_E521_TEST/`）跑 `project_inspect` → 验证离线读取/IL 工具对 H5U 工程（.hcp 同源格式）是否直接可用（预期读取类大概率可用、`native_compile_copy` 明确拒绝）；
- 看点 3：用 `native_compile_probe` 静态列出本机编译 DLL 导出函数 → 判断 H5U/LiteST 编译入口与 H3U 是否同名（决定适配工作量）。
- 预期结论形态：读取/补丁工具链可用 ⇒ WSL↔Windows 混合管线（WSL 生成 IL/符号 → Windows 无 GUI 编译）有落点；编译 DLL 完全不同 ⇒ 回退 AutoShop GUI（现有 `autoshop_ui.ps1`）。

### 3.6 USB 口 Linux 侧侦察（顺手做，5 分钟）

```bash
# Windows: usbipd list  →  usbipd bind -b <busid>  →  usbipd attach --wsl -b <busid>
lsusb                                   # 记录 VID:PID（全网无公开记录，此处即首发数据）
dmesg | tail                            # 预期 "cdc_acm ... ttyACM0: USB ACM device"（若走 CDC）
```
预期：Easy521 枚举为 /dev/ttyACM0；再向该口随意发字节看是否有应答。**注意**：即便拿到 ttyACM，也不等于编程协议可复刻（§1.5）；此实验的价值是留档 VID/PID 与枚举类型，并彻底关闭"Linux 直连 USB 编程"的想象空间（除非未来有人逆向出协议）。

---

## ④ 结论：三条能力链在纯 WSL 下的可行性

| 能力链 | 纯 WSL 评级 | 依据 | 可行替代 |
|---|---|---|---|
| **监测**（读 D0-17 / M / Y 状态） | **可行 5/5** | Modbus TCP 502 出厂即开、协议完全公开、地址映射官方手册+Neuron+HslCommunication 三方互证、pymodbus 3.16.1 在 WSL 无障碍（§1.2、§2.7、§3.1） | ——（无需 Windows 参与） |
| **烧录**（下载编译产物） | **不可行 1/5** | AutoShop 私有以太网/USB 编程协议零文档零逆向（§1.4、§1.5）；无任何开源下载器存在；自研=从零逆向私有二进制协议，风险（变砖/合规）与工作量均不可控 [官方+联网核实] | AutoShop 以太网/USB 下载是官方功能、一次点击（01 文档 C 节）；保留 Windows 侧 GUI（人点或 `autoshop_ui.ps1` 辅助）；程序变更低频时成本可接受 |
| **编程**（代码→工程→编译） | **基本不可行 2/5** | 无开源 LiteST 编译器；AutoShop 无公开 CLI/SDK/COM 接口 [官方·无记载] + [未证实任何第三方实现]；唯一先例 autoshop-mcp 证明"无 GUI 编译"可行但需 Windows + 本机 DLL，且当前 profile 仅 H3U、明确不含 H5U/ST（§2.4） | 短期：Windows 侧 AutoShop GUI（现有资产 E2 脚本辅助）；中期：按 §3.5 评估 autoshop-mcp 的 H5U 适配，打通后 Windows 侧可无人值守编译（仍需 Windows，但无 GUI） |

**总判断**：
1. "纯 WSL 全链闭环"（写码→编译→下载→监控）**当前不存在合法捷径**，卡点集中在编译器（闭源 DLL）与下载协议（私有未逆向）两环，且开源社区没有任何项目绕过这两环。
2. **监测链应立即 WSL 化**（pymodbus + 真机 502，§3.1 顺带敲定浮点字序），这部分与 AutoShop 彻底解耦，收益最大、风险为零。
3. 编译+烧录维持"Windows 最小触点"：要么继续 AutoShop GUI + 现有 UIA 脚本，要么投入评估 autoshop-mcp 的 H5U profile 适配（MIT、方法论完备，是唯一值得跟进的开源变量；建议 subscribe 该仓库观察 H5U/ST 支持动向）。
4. PLC4X 无汇川驱动的传闻已证伪；plc4py 不可依赖；EMQX Neuron 的价值在文档与备用网关，不在必选栈。

---

## 附：主要来源

- [Apache PLC4X 仓库与驱动列表](https://github.com/apache/plc4x) / [PLC4X Modbus 协议文档](https://plc4x.apache.org/plc4x/0.12.0/users/protocols/modbus.html) / [PLC4X 官网](https://plc4x.apache.org)
- [EMQX Neuron · Inovance Modbus TCP 驱动（含地址映射表）](https://docs.emqx.com/zh/neuronex/latest/configuration/south-devices/modbus-hc-tcp/modbus-hc-tcp.html)
- [Mech-Mind · 汇川 H5U/EASY 系列 Modbus TCP 通信配置](https://docs.mech-mind.net/zh/robot-integration/latest/standard-interface-plc/modbus-inovance-setup-instructions.html)
- [HslCommunication（dathlin）](https://github.com/dathlin/HslCommunication) · Inovance 地址示例：`HslCommunicationDemo/PLC/Inovance/Helper.cs`
- [autoshop-mcp（JonathanChan-geek）](https://github.com/JonathanChan-geek/autoshop-mcp) —— 无 GUI 编译 H3U 工程，MIT
- [inovance_tag_cyg](https://github.com/ChangYuan-Semiconductor-Equipment/inovance_tag_cyg) / [Protocol-Inovance](https://github.com/mipu1711/Protocol-Inovance) / [ModModbusTcpH5U](https://github.com/lemonchen2021/ModModbusTcpH5U) / [HcPlcApi](https://github.com/ChinaR0C/HcPlcApi) / [electrical-copilot](https://github.com/clei32879-gif/electrical-copilot) / [plc-sweep](https://github.com/agentthink/plc-sweep)
- [Inovance EU · Easy 系列 PLC 宣传册 PDF](https://www.inovance.eu/fileadmin/downloads/Brochures/EN/Easy_Series_PLC_Br_EN_Singles_Web_V0.1.pdf)（Type-C 编程口官方描述）
- [CSDN · 汇川 EASY 系列以太网 Modbus TCP 从站（端口 502 固定）](https://blog.csdn.net/weixin_42946146/article/details/146376140) / [CSDN · AutoShop 连接 PLC 下载步骤（以太网）](https://blog.csdn.net/duxiaokang2014/article/details/140917286) / [CSDN · H5U TCP 服务端通信](https://blog.csdn.net/weixin_39180540/article/details/125408691)
- [pymodbus PyPI](https://pypi.org/project/pymodbus/) / [pymodbus 文档](https://pymodbus.readthedocs.io)
- 本机交叉信源：`docs/wsl-feasibility/01-protocol-facts.md`（官方编程手册 p45-64、p386-408、p802-810、p952、p961-983；Easy 手册 p99-123；H5U USB 驱动安装说明）
