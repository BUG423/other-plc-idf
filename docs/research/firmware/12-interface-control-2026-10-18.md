# Easy521 无 GUI 通信入口静态追踪（2026-10-18）

本轮把 `IH530Communicate` 从导出名推进到实际工厂、实现对象和读运行态命令。确认可继续构建独立宿主的代码路径；尚未得到足以安全调用的完整初始化/枚举/传输契约。另发现安装包自带的本地变量共享服务，但没有把它认定为 PLC 控制 API。

仅读取本机合法安装文件，没有加载通信 DLL、执行服务/客户端、操作 GUI、打开套接字或访问 PLC。未找到安装目录内的 SDK 头文件或 C/C++/C#/Python 示例；文件哈希、RVA 和证据等级见[结构化记录](12-interface-control-evidence-20261018.json)。以下地址全部是本轮固定二进制的 RVA，不能直接作为另一版本的调用地址。

## 1. IH530 包装器必须先有真实工厂

`InterfaceMgr.dll` 的 `IH530Communicate::SetFactory`（RVA `0x68080`）把输入指针写到全局 factory。`Instance`（`0x68060`）读取该全局、解引用虚表、转到工厂虚表偏移 `+4`；包装器没有在此前检查空指针。这是仅加载 InterfaceMgr 再裸调 GetPLCRunMode 不完整的直接代码证据。

`H530Communicate.dll` 本身没有命名导出，但存在完整注册路径：CRT 初始化表位置 `0x749f4` 指向函数 `0x41770`，它构造派生工厂；工厂构造函数 `0x7490` 在调用基类后，于 `0x74cd` 调用导入的 `IH530Communicate::SetFactory`。RTTI 确认类名为 `CH530CommunicateFactory_HC`，其虚表为 `0x793d8`。

工厂 `+4` 槽位指向 `0x7520`：首次分配 12 字节实现对象，调用构造路径 `0x3a70` 并缓存。实现对象 RTTI 为 `CH530Communicate_HC`，虚表 `0x791fc`。工厂注册只是初始化的一部分；线程/任务池、MFC 状态、错误结果全局以及传输资源的生命周期仍需完整契约。此处没有通过“试着调用”检验这些前置条件。

## 2. 最小只读目标：GetPLCRunMode

这条调用的静态链已经闭合：

| 层 | 已定位事实 |
| --- | --- |
| 静态包装器 | InterfaceMgr `0x69a80`，四个入参传递给实现对象 |
| 实现虚表 | `CH530Communicate_HC` 的 `+0x1b8` 槽位 |
| 实现函数 | H530Communicate `0x7310`，构造 48 字节命令对象 |
| 命令 RTTI | `CRTUStateCommand`，虚表 `0x7bb24` |
| 执行路径 | `0x133d0` 调用共同命令执行层，逻辑命令编号 **`0xff010000`**，预期数据长度 **4 字节** |
| 结果写回 | 将返回缓冲区第一个 DWORD 原样复制到调用者 `unsigned long*`，随后释放该返回缓冲区 |

包装器的导出签名和压栈调用点一致：返回 32 位 `long`，入参为 `unsigned long*`、`CWnd*`、`EnumExecuteMode`、`EnumCommandLevel`。这比需要未知结构大小的 GetPLCInfo 更适合作为未来独立宿主首个只读验收目标，但不能据此猜两个枚举值或把返回 long 当运行状态。

逻辑命令编号是送入厂商内部执行层的值，**不是已完整恢复的 USB/TCP 线帧**。尚未确认请求头、序列号、校验、鉴权、返回错误布局和 RUN/STOP 数值枚举。该层不把状态值翻译成 RUN/STOP。交叉复核还发现反证：本机 Simulator 的 mp_core 将同数值 `0xff010000` 映射为 EtherCAT 扫描状态/控制字，而 CPU 状态候选索引为 `0xff000000`。因此这里的 IH530 逻辑值不能直接搬作 Simulator 索引；须恢复共同执行层的封装/转换并证明版本关系。相关映射由 14 号研究独立核对；当前不能用数值相等互证协议兼容。

`GetPLCInfo` 的 facade RVA 为 `0x683f0`，转到实现虚表 `+0x44`，实现 RVA `0x42f0`；`_H530_PLC_INFO` 完整布局仍未知。`SimpleCommand` facade `0x69290` 转到实现虚表 `+0x128`；其 `EnumH530SimpleCmdType` 尚未确认，不能将任意数值写成 RUN/STOP。

## 3. USB/网络选择仍缺命名枚举和结构契约

`SetCommunicatType` 的 facade `0x680d0` 进入实现 `0x3cd0`。代码明确保存类型整数并对 0 与非 0 分流到不同对象；另一个配置路径 `0x3d20` 对数值 4 进行判断，并读取 EthernetInfo 的 `+0/+4/+8/+12/+16` 五个 DWORD。没有据此给 0、4 擅自命名，也没有把观察到的字段偏移当作已恢复的完整结构。

下一步精确所缺为：`EnumCommunicaterType`、`EnumExecuteMode`、`EnumCommandLevel`、`tagEthernetInfo`、工厂/线程池初始化与清理顺序。厂商若能提供这些声明与32位宿主只读示例，即可围绕 GetPLCRunMode 建立最小、可审阅的适配器；目前不足以写入可执行的 ctypes 试探代码。

## 4. 本地 AutoVarShared 服务候选

安装包 `AutoVarShared` 中确实存在 `InoVarSharedService.exe`、`PluginCommunicate.dll`、`PluginGlobalFunc.dll`、`Plugin.dll`、相应 PDB 和 `TestITPClient.exe`。已确认的静态事实：

- 服务 EXE 使用 Windows Service API，并经 `PluginManager` 注册、加载插件；可见的插件包括 PluginLogger 和 PluginCommunicate。
- PluginCommunicate 导出 `CreatePlugin` 工厂，含 IOCP 监听/收发处理、JSON 解析及包头/CRC 检查路径。
- 服务监听相关代码使用 `127.0.0.1`，服务端口通过注册表子键 `SOFTWARE\AutoServicePort` 和 `GetServicePort/SaveServicePort/GetAvaliablePort` 管理；不能猜一个固定端口。
- PDB 存在 `CCommunicateBase::GetPort/InitListen`、`CPacketHandler::HandlerRegister`、`CVarDataMgr` 等符号。现成测试客户端有 Path、VarSize、comment 等变量元数据相关字段线索。

这些证据指向跨进程的变量共享/数据路由服务。它是否在没有 AutoShop 或其他数据提供进程时直接读取设备、是否暴露 RUN/STOP，均未确认。现有安装目录顶层的 InstallService/StartService 脚本实际针对另一个 LogCenterSer，不能拿它们作为启动 AutoVarShared 的指令。

本轮随后解析了匹配 PDB 的包头、平台/操作枚举与注册 JSON，结果见下一节。客户端与真实设备之间的数据提供关系仍未闭合；继续把此入口标为“本地变量服务候选”，不把本地变量元数据读写当成设备运行监控。

## 5. 匹配 PDB 给出的本地服务结构

使用根代理从 Ubuntu 官方包离线解出的 LLVM20.1.2 `llvm-pdbutil` 读取本地 PDB。PluginCommunicate.pdb 未剥离，GUID `{BF00E18A-C93D-47B3-A8E8-5F4C5EFAD741}`、Age=1，与该 DLL 的 CodeView RSDS 完全匹配。全量符号和反汇编留在 `.plcidf/research-tools/pdb-dumps`，仓库只记录本任务相关字段/地址。

PDB 的 `PacketHeader`（type index `0x2035`）是 packed、大小 **28 字节**：

| 字段 | 类型 | 偏移 |
| --- | --- | ---: |
| dataMagic | u32 | 0 |
| dataLength | u32 | 4 |
| dataType | i32 枚举 | 8 |
| platform | i32 枚举 | 12 |
| packetID / connectID / iVersion | u16 / u16 / u16 | 16 / 18 / 20 |
| sSocket | u32（未对齐） | 22 |
| dataCrc | u16 | 26 |

PDB 定位的构造函数 RVA `0xfb50` 与二进制交叉核对，确认 magic 为 `0xa50ea50e`，版本默认 1，socket 默认 `0xffffffff`。包头处理函数 `0xfdc0` 确实校验该 magic，并以 `28 + dataLength` 计算接收长度、单次上限 1460。`dataLength` 大于单次容量的分包状态未完整恢复，不能把本段当作可直接发包的全部契约。

`g_GenCrc16Code`（RVA `0xfbc0`）使用初值 `0xffff`、右移与多项式 `0xa001`，返回前交换高低字节。完整包检查路径对空数据计算头部前 26 字节 CRC，对非空数据计算本次数据（上限 1432 字节）CRC；未进行真实抓包交叉验证。

`_PACKET_TYPE_` 的命名值为：0 NULL、1 REGISTER、2 FULL_AMOUNT_REQUEST、3 FULL_AMOUNT_DATA、4 FULL_LAST_AMOUNT_DATA、5 UPDATE_FULL_DATA、6 DATA、7 ANSWER、8 CLOSE、9 ERROR、10 FULL_AMOUNT_INCREASE_DATA。`_PACKET_PLATFORM_` 只有 **PLC=0、INOTOUCHPAD=1**。它们是本地服务的角色/数据路由枚举，不能与前述 IH530 的传输选择枚举混用。注册处理函数 `0x10ef0` 的代码确实从 JSON 读取 `Path`；响应路径使用 `Response`。

匹配的类型和命名处理函数支持“本地 PLC/HMI 工程侧变量同步服务”的解释。该服务枚举没有 RUN/STOP 命令，也未闭合直接访问设备的上游数据提供关系。它不是已经验证的独立 PLC 控制入口；可进一步调查其变量元数据同步是否能供无 GUI 工具使用，但不能据此宣称可读 PLC 实时值。
