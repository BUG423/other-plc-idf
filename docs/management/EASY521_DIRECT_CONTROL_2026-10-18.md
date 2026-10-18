# Easy521：最简单直接的控制路线与本轮验证

2026-10-18。按此前实物 **Easy521** 研究；本轮用户写的 Easy512 尚未独立确认为另一机型。三个 AI 技术代理并行分析，统筹复核与现场只读枚举；没有聘用外部工程人员。本轮没有下载、RUN/STOP、固件操作或向 PLC 发送协议请求。

**当前最短的已知接口是 Linux → Modbus TCP → PLC 的 D/M 内存。** 它能承载参数与业务命令；实际效果取决于 PLC 应用是否使用这些地址。实现任意新 ST 的编译、下载与执行，仍需完整应用工具链。

## 从第一性原理分清“控制”

PLC 可以看成一个周期执行程序的控制计算机。外部请求至少分为四层，验收证据不同：

| 要实现的结果 | 最短候选路径 | 已知支持与缺口 |
| --- | --- | --- |
| 读写数据、给现有程序设置参数/业务 START | Modbus TCP，默认502 | 手册明确 D/M 地址映射；需真实 IP、可达服务及应用地址绑定 |
| 切换 CPU RUN/STOP | 厂商 IH530 通信层；物理开关 | 找到实际实现与状态查询链；独立初始化、枚举及线上事务尚未闭合 |
| 下载 AI 新写的 ST 应用 | 文本准备 → StLang → 链接/包装 → 厂商文件事务 → 独立校验 | 前端已实际生成 ARM 对象；完整应用链接、文件事务和设备字节绑定仍缺 |
| 替换 PLC 系统并直接驱动 I/O | 对应板卡的启动链/BSP/运行时/I/O 驱动 | 准确 SoC、系统镜像、板级 I/O 和可恢复性未确认 |

读回一个刚写入的数值，只能证明该时间点服务返回了这个值；不能证明 CPU 正在扫描、业务动作已执行、数值掉电保持或端子有电。业务 START 的含义由应用定义。M8000 是只读运行标志，且不在默认 M0–M7999 的协议映射范围，不能作为写线圈启动 CPU。

## 最短数据控制入口已经具体化

本机官方编程手册第428页给出默认 Modbus TCP 服务器、端口502及 D0–D7999 → 原始 holding-register 偏移0–7999。D100 的报文偏移就是100，不是40101。M区使用位功能；不能把相同数值的位地址和字地址混用。完整地址/页码/版本哈希见[手册路线报告](../research/firmware/13-documented-control-routes-2026-10-18.md)。[EMQX 自身的 Easy521 接入说明](https://docs.emqx.com/en/neuronex/latest/configuration/south-devices/modbus-hc-tcp/example/autoshop/autoshop-modbus.html)及[驱动地址表](https://docs.emqx.com/en/neuronex/latest/configuration/south-devices/modbus-hc-tcp/modbus-hc-tcp.html)交叉支持该数据接口。

新增独立工具 [easy521_d_control.py](../../tools/easy521_d_control.py)，使用现有固定版本 pymodbus，在 Linux 中只处理 D 区的16位原始值。它与原来的只读 monitor 分离：

```bash
# 从仓库根目录运行。把环境变量设为已核实的 PLC IP。
export PLC_IP='<实际PLC的IP>'
.venv/bin/python tools/easy521_d_control.py read --host "$PLC_IP" --d 100 --count 2

# 查看单次写入计划；默认不连接。
.venv/bin/python tools/easy521_d_control.py write --host "$PLC_IP" --d 100 --value 123

# 仅对已确认用途的应用地址执行；写一次，再读回。
.venv/bin/python tools/easy521_d_control.py write --host "$PLC_IP" --d 100 --value 123 --execute
```

这里的 D100 是地址语法示例，没有为本设备分配 D100，也没有在本设备执行这些命令。预览不访问网络；实际写入关闭重试。写出后超时、错误回显或读回不符均不能按“设备没改变”处理；工具不重放。它输出内存交互结果，不声称目标型号已认证或应用正在执行。

现有归档 BESS 工程19个 `g_*` 变量为私有，软元件地址为空。此前设计的 D0–D17 CSV 是接口方案，不能替代实际工程绑定。最小落地方式是一次性建立明确的命令、参数、状态和程序版本地址表，应用在扫描周期内解释业务命令并返回确认；后续 Linux 可直接控制。选址前须核对当前应用，不能随意覆写已有数据。需要更改算法时，仍走独立的新应用部署流程。

FINS TCP/UDP 默认9600，但所查 Easy 实现仅建立0101/0102内存读写；UDP要求 CPU 已 RUN。OPC UA 可按名称访问已发布变量，需先在工程激活并公开变量，默认4840；手册明确其服务可在 CPU STOP 后继续。现有私有变量尚不能据此直接访问。当前选择 Modbus 是因为它的本机文档映射和最小客户端都更明确。

## 脱离 AutoShop 界面的研究推进到了哪里

[通信层报告12](../research/firmware/12-interface-control-2026-10-18.md)记录厂商实际工厂初始化、实现虚表、查询命令和本地变量共享服务。导出函数名不足以直接调用：IH530 facade 依赖已注册工厂与实现对象，缺初始化可能空指针崩溃；RUN状态返回值枚举也尚未标定。本地 AutoVarShared TCP 服务的 PDB 将其指向工程/触摸屏变量同步，尚未建立 PLC CPU 控制能力。

[下载链报告14](../research/firmware/14-easy521-download-paths-2026-10-18.md)从真实历史日志确认18个文件成功项，并定位 AutoShop 中31处 `DownloadFile` 调用。应用包含对象、变量和多个配置文件，不能只传 MAIN.o 或 Output.prg。不同目录的同名文件版本不一致，当前文件名匹配不能绑定到历史传输字节。已找到独立本地仿真器命令行和回环协议解析入口，适合下一轮隔离实验；其协议与真机兼容性仍未证明。

下一步真机无界面部署的具体工作是：恢复精确初始化/通信参数契约，追踪每项文件源路径及变换，确定完整提交顺序，再用冻结应用进行独立程序/字节校验。CPU模式控制与文件下载分别验收。前一轮普通 ST → ARM 对象的实际结果见[编译交付](HOST_TOOLCHAIN_DELIVERY_2026-10-18.md)，不能将对象成功编译扩写为完整 PLC 应用成功。

## 本轮现场状态

22:41（Asia/Shanghai）的 Windows 只读枚举显示 Realtek USB GbE 物理网卡 `以太网 2` 为 **Disconnected、0 bps**，IPv4 为169.254.25.85/16。因此没有对猜测的 PLC IP 或 WSL 虚拟网关做扫描。见[网口原始记录](../../evidence/easy521-control-20261018/wired-network.json)。

USB 同轮枚举为 **8-3 / 0471:a531 / H5U USB Device / Not shared**；历史位置6-3现在是输入设备。见[USB原始记录](../../evidence/easy521-control-20261018/usb-enumeration.json)。枚举名和VID/PID不独立证明准确PLC型号、固件、CPU状态或供电情况。本轮没有沿用旧位置进行写操作，也没有更新旧证据。

本轮未重新观察供电、RUN灯或接线。此前用户报告 USB-only、无外接24V、无输出负载、开关RUN/RUN灯灭；此前两次RUN请求拒绝是真实历史事实。额定主电源为24VDC±10%，手册明确 USB-only 不支持掉电保持，但没有证明 USB-only 必然禁止 CPU RUN。因此缺24V仍不能写成已证实的唯一根因。

## 芯片与系统的结论

[汇川 Easy521-0808TN-INT 官方页](https://www.inovance.com/global/content/details_816_556156.html)明确四核处理器、ST与工业数据协议，没有披露准确SoC或BSP。历史 `Linux 3.10.65 with sunxi_usb_udc` USB字符串和ARMv7对象是平台线索，尚未形成板卡/芯片的独立交叉证据。[官方固件来源复核](../research/firmware/04-official-sources-2026-10-17.md)取得的是资源元数据，未取得匹配本设备的系统镜像。本轮重新打开官方入口仍是动态页面，不把它写成已获取固件。

要直接驱动核心硬件，还需确认板上处理器、运行系统、I/O连接方式、看门狗和输出提交规则。即使确认Linux，普通ARM编译也不会自动获得这些板级能力。取得对应硬件修订的官方镜像/BSP、许可与源码获取途径，或有规格依据的只读板级证据，才会实质缩小这条路线；本轮没有进行拆机、BootROM、系统擦写或恢复实验。

## 本轮验收与后续工作

本轮现有工具测试68项通过（含新增D区回环14项），只读monitor回归82项通过，两组不重叠共150项。新协议测试仅连接本机回环模拟服务器，覆盖真实报文、读回、不一致、异常响应、超时与不重复写；具体命令与结果见[本轮验证记录](../../evidence/easy521-control-20261018/software-validation.json)。独立审计检查了固定版pymodbus的连接/重试路径，未发现阻塞问题。这些验证客户端行为，不替代Easy521硬件验收。

- 已交付：可运行的Linux D区工具；三份逐项静态/手册研究；现场只读连接证据与这一决策报告。
- 待链路恢复后：确认PLC实际IP及服务，先读已知内存，建立应用地址和身份绑定，再执行明确的内存业务试验。
- 持续研究任务：独立IH530宿主初始化/模式枚举、18文件完整事务、应用字节校验、隔离仿真器协议、芯片与系统证据。分别跟踪T07/T08/T09/R01/R02；均未因本轮研究关闭。

GitHub同步记录在PR #13及总跟踪Issue #12。工作流合入默认分支前，定期进展汇报仍未启用；本轮交付不代表有无人值守的持续研发进程。
