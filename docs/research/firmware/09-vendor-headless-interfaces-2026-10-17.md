# Easy521：无 AutoShop 窗口的编译与设备接口复核

日期：2026-10-17。只读本机厂商安装文件、PE 导出/导入表、导入库符号与官方公开资料；未运行厂商程序、加载 DLL、访问 Windows 进程/GUI/USB/PLC，也未进行下载、运行控制或固件操作。厂商二进制、机密说明书及其接口定义不复制到仓库。新取证路径、SHA-256、选定导出符号与导入依赖见 [独立台账](09-vendor-interface-inspection-20261017.json)。

## 本次得到的具体入口与否决证据

| 文件 | 新观察 | 实际意义与未解决项 |
|---|---|---|
| `xml/PlcSeriesModel.xml` | GB18030 XML 将 `H6UA8` 的 alias 映射为 `Easy521`；同时有 `device/H6UA8.dll` | 确认 AutoShop 内部机型标识，可核对工程/SDK 的目标选择；它不是 SoC 型号 |
| `Bin/STLang/StLang.exe` 与 `STLang_New` | 均为 PE32 x86 **console** EXE，导入 `Kernel.dll` 和 Windows/VC 运行库，未静态导入 AutoShop 主程序 | 有独立子进程编译的具体候选；未取得真实 argv/输入契约，未执行新编译，因此不能登记为已可用 CLI |
| `InterfaceMgr.dll` | 4534 个命名导出；有 `CSTCompiler::StartSTPretreatCompile`、数据中心/指令查询器/文件读取回调设置，以及 `IH530Communicate` 的静态入口 | 实际存在 ST 前处理、设备读回与文件传输的 ABI 线索；编译依赖完整工程模型，不能只传正文字符串就认为已替代 GUI |
| 同一 `InterfaceMgr.dll` | 静态 `IH530Communicate::GetPLCRunMode`、`GetPLCInfo`、`GetCPUID`、`DownloadFile`、`UploadFile`；另有相应虚成员接口 | 可以把“独立只读运行态读回”定为首个设备 SDK 验收；仍缺初始化、传输类型/执行模式枚举、结构布局与机型适配证据 |
| `CommunicaterModule.dll` | 95 个命名导出，包含 `GetExecMode`、`GetVersionInfo`、`ProgramValidate`、下载与上载 | 是另一套更通用/历史通信接口，不能据名字视为已支持 Easy521；也不能绕过其初始化和 MFC 依赖 |
| InoLoaderPro `MainControl.dll/.lib` | DLL 有 88 个命名导出，其中 6 个平坦 C 名称；导入库符号为 `_burn`、`_scan` 等，没有 `@N` 后缀 | 与 Win32 cdecl 命名一致；真实参数类型、结构、回调及用途仍未知，不能凭函数名写一个猜测的 ctypes 调用 |
| InoLoaderPro `linkcfg/tools.xml` | `PLC` 产品线 id `14` 当前只列 `Gx20` 通信耦合器 `0x60A`，未列 Easy521/H5U | **明确否决“现有 InoLoaderPro 已证明可用于 Easy521 应用下载”的说法**；目录名或 `burnApp` 都不能替代型号支持表 |
| AutoShop 安装树 Linux 主库盘点 | 找到 `MainControl.dll/.lib` 与 x86-64 的 `libIvtAes.so`，未找到 `MainControl.so` | 本地没有已验证的 Linux 主控制 SDK；辅助 `.so` 不提供完整下载/监控能力，也不是 ARM 设备固件 |

平坦导出名称为 `burn`、`scan`、`getBinfile`、`setParameters`、`setBurnCancel`、`setBurnProgessCallBack`；同 DLL 还可观察到 boot 烧录和整片擦除类名称。因此 `burn` 不能自动归类为 PLC 用户工程下载，`scan` 也不能自动认定为无副作用的普通设备发现。以上仅为导出表事实，没有执行这些入口。

MSVC 的 Win32 C 名称修饰可帮助区分调用约定，但没有携带完整 C 参数信息；C++ 名称中的 MFC/STL 参数还要求匹配架构、编译工具集、对象生命周期和运行库。[Microsoft 名称修饰说明](https://learn.microsoft.com/en-us/cpp/build/reference/decorated-names?view=msvc-170)

## Windows 后台可否独立于 GUI

**有具体实现切入点，尚无已验收的 Easy521 无 GUI 闭环。** 本机导出表比“某处可能有 SDK”的猜测更进一步，但不能证明 DLL 在缺少 AutoShop 初始化和消息循环时能正确工作。`GetPLCRunMode` 同时具有静态封装和虚成员接口，可能有助于减少外部对象构造；其实际内部依赖仍待厂商头文件/示例或独立宿主验证。

编译需要分两层：`CSTCompiler` 负责工程上下文/前处理，`StLang.exe` 是后续 console 编译候选。现有工作副本 `STTemp/MAIN.st` 有 `****AS*****` 容器前缀，不是任意 UTF-8 ST 文本；不能用修改扩展名替代厂商输入生成。二进制里包含 ARM/Cortex 目标名称也可能只是 LLVM 支持表，不证明 Easy521 的具体 CPU。

官方检索页确实列有 V4.12.0.2，名称中的“后台编程软件”是软件标题，不能解释为承诺命令行或无窗口 SDK。[AutoShop 官方检索结果](https://www.inovance.com/portal/allResult?key=AutoShop) 官方 H3U 的 `.down` 示例仍经 AutoShop 生成与下载，并明确针对对应型号，不是 Easy521 的公开原生下载接口。[H3U 官方示例](https://www.inovance.com/global/content/details_2349_583466.html)

## 可以立即安排的实验

1. **离线编译子进程参数验证**：在隔离工作副本上，由执行者启动有硬超时的 `StLang.exe` 帮助/无参数探测，保存 argv、stdout、stderr、退出码与输出文件哈希；然后取得一次真实厂商构建所用的精确 argv 和冻结输入。用同样输入独立启动 console 编译器，要求正确源码生成本次新 `.o/.rst`、错误源码失败。不得将内置 LLVM 帮助字符串或进程退出 0 当作编译通过。本轮未启动该程序。
2. **优先取得只读设备 SDK 契约**：具体向厂商索取 H6UA8/Easy521 对应的 `IH530Communicate` 初始化/释放、USB 传输选择、同步读回、枚举和结构声明及 32 位宿主示例。首个实验只实现设备信息和实际 RUN/STOP 状态读回，与 AutoShop 同时刻结果比对；不调用下载、写变量、改网络、切升级模式或 boot 类入口。
3. **应用下载另立验收**：只有 SDK 明确支持 Easy521 用户工程时，才用厂商原生成物做目标身份、程序完整性、一致性读回和运行证据核对。`MainControl` 当前型号表不满足此条件；不将其烧录能力并入现有 `flash`。
4. **Linux 正常获取请求**：索取同版本 Linux 主控制库、头文件/示例、宿主架构、依赖、许可及 Easy521 应用层能力表。仅拿到辅助 AES 库或其他机型支持包不算完成；本轮未联系厂商或绕过下载注册。

目前可立即执行的是第一项的离线 console 验证；第二项已有精确函数族和内部机型供接口问询，尚不能提供可信的直接调用参数。以上是下一实验的具体输入和验收，不冒充本轮已完成调用。

## 本轮 USB 单独供电的官方手册证据

本机用户手册资料码 `PS00006444A08`，PDF 页码与印刷页码一致，SHA-256 为 `e793971a545f5b4c89e067957ac73973546f6df354f586a62c041995df5dc5da`。Easy52X 专章对应 Easy521/522/523，型号列表在第 135–136 页。

- 第 138 页明确写有“当只有USB供电时，不支持掉电保持”。它证明手册讨论了 USB 单独供电，限制针对掉电保持。
- 第 139 页列明 Type-C 可进行用户程序上下载及固件升级；端子输入规定为 24V DC±10%，即 21.6–26.4V。第 140、141 页分别列出数字输入、输出的 24V 电压等级。
- 第 137 页说明 RUN 灯显示用户程序是否运行，拨码开关用于运行/停止控制；拨到 RUN 与软件实际读回运行态是不同证据。

所查 Easy52X 章节没有明确给出“只有 USB 时 CPU 一定不能运行”或“一定可以完整运行”的结论。不能只用 24V 端子额定值推断 USB 当前 CPU 状态，也不能把 USB 枚举成功视为物理 I/O 或以太网已经正常。实际 CPU 运行态由本轮另行真机读回确认，结果不在本只读文件调查中预填。
