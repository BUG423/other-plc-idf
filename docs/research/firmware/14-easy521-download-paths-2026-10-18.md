# Easy521 最短应用下载路径：文件集、直接调用点与本地仿真协议

2026-10-18。只读已安装文件、工程文件和已有真实下载证据；使用 Linux `objdump`、Python 读取 PE/COFF 表和指令。未运行厂商 EXE、动态加载 DLL、操作 GUI、USB、网络设备或 PLC。文件哈希、调用地址、18 项文件对应表和复现命令见[元数据](14-easy521-download-paths-20261018.json)。不包含厂商二进制或完整接口文档。

**最值得继续的真机路径是：冻结完整应用文件集 → 独立 IH530 文件传输宿主 → 独立程序/字节校验。** 本轮把候选缩小到了 AutoShop 实际使用的 31 个 `DownloadFile` 调用点和分阶段文件集，但没有完成无 GUI 下装。现成 CLI 中找到的是本地仿真器启动入口；没有把它当作 Easy521 上传工具。

## 真正发生过的下载有 18 个文件成功项

历史真实证据 `evidence/easy521-application-20261017/150900127646-production-download-reconcile.json` 的通讯日志共有 20 条信息：开始、18 个文件成功项、下载成功终态。

| 类别 | 日志中的文件名称 |
|---|---|
| 工程源码 | `project` |
| 程序与变量 | `userprog.foid`、`stprog.o`、`stuserprog.o`、`symbol.sym`、`varsinfo.foid` |
| 配置 | `syscfg.foid`、`ethercat.foid`、`modbus.foid`、`canlink.foid`、`modcfg.foid`、`EIP.dat`、`EIPIns.foid`、`ecttrans.foid`、`dataLog.foid`、`OpcUAServer.dat`、`sftpserver.foid`、`Ecam.foid` |

这比只检查 `Output.prg/sdt` 明确得多。当前工程中的候选散布在 `PLC/`、`Temp/`、`Temp/DownLoad/`、工程根目录及 `OnlineVersion/V0/`；元数据逐个记录了同名候选的长度与 SHA-256。`project` 未找到可直接按名称绑定的本地文件，不能把 `.hcpp` 自行认定为传输字节。

**当前这些目录并不代表同一版本。** 静态读到的 `Temp/stprog.o` 为 127,464 字节，`OnlineVersion/V0/stprog.o` 为 127,452 字节；`Temp/stuserprog.o` 为 6,240 字节，`PLC/stuserprog.o` 和 `OnlineVersion/V0/stuserprog.o` 均为 6,228 字节。后一组虽然相差 12 字节，前后去除 12 字节都不相等，重叠部分还有 2,997 字节不同；不能解释成固定头尾。两组 `.o` 均从偏移 0 开始为 ELF。

此外，`Temp/userprog.foid` 为 294 字节，`PLC/` 和 `OnlineVersion/V0/` 中同名文件为 215 字节；同名 `symbol.sym` 也有不同哈希。它们是当前文件观察，不是历史 USB 字节抓包。直接传输前必须先确定每项源路径、打包/变换、会话和提交顺序，防止把多个阶段的同名文件拼成一套应用。

现有 PLC upload ZIP 的 62 项主要是可恢复的工程输入；其中没有 `stprog.o` 或 `stuserprog.o`。它不能充当已验证的设备应用字节备份或字节回读。原备份仍可保留，但恢复能力没有在本轮增加任何通过声明。

## 直接传输入口已经定位到真实调用者

AutoShop.exe 实际导入 `InterfaceMgr.dll` 的静态 `IH530Communicate::DownloadFile`，IAT VA 为 `0x1eb11d8`。扫描到 31 处直接 `call [IAT]`，主要集中在 `0xe64016` 至 `0xe74328` 一带；完整地址列表在 JSON 中。对 `0xe64016` 和 `0xe73189` 的独立反汇编确认它们是实际调用指令，返回后有 `add esp,0x18`，不是单纯字符串命中。

这提供了下一轮可执行的静态目标：沿这 31 个调用点向前追踪 CString 本地路径、远端名、窗口参数和执行参数，向后追踪错误处理及最终提交命令。直接传输层与 GUI 编译动作存在可分离边界，但**尚未证明完整下载事务能在独立宿主中正确初始化、跳过编译且仍完成一致性校验**。ABI/初始化/枚举由并行 IH530 研究继续负责；本轮没有猜 ctypes 签名。

`CommunicaterModule.dll` 的另一套 `CCommunicaterLocal::ProgramValidate` 位于 VA `0x10009700`。该函数体可见：分配 `0x30` 字节对象，调用构造器 `0x1001f490`，再于 `0x10009778` 调用 `0x10007be0`，后者对应导出的 `RunCommThread`。构造器写入 vtable `0x100651fc` 和偏移 `+0x2c` 的常量 `0x55`。本层没有观察到编译器调用，也没有编译器相关导入。

但 AutoShop.exe 没有直接导入这个 `ProgramValidate`，且本轮没有证明此旧通信类适用于 Easy521。常量 `0x55` 只是构造字段观察，不是已识别的 Easy521 命令号。不能用这个接口替换当前 GUI 的“程序校验”，也不能由此声称已避开自动重编。

## H6UA8 与下载设置能排除哪些误区

`device/H6UA8.dll` 的 5 个导出是 `GetModuleIdentifier`、`ReadHardwareInfo`、`ReadIstructionInfo`、`ReadSysDataType`、`ReadSysVar`。`.text` 原始长度仅 16,896 字节，资源节为 3,223,552 字节，资源类型包含硬件配置、指令描述、模块标识和系统变量。它应作为 Easy521 模型/指令资源入口研究；本轮没有在这里找到应用下载导出。

安装目录 `config.ini` 有 `[DownLoadFile] Show=1, Hcpp=1` 和 `[CreateDownLoadFile] Show=1, Hcpp=1`。名称与现场“下载提示/工程源码”选项相符，但没有证明这是受支持的无窗口参数或防止重新编译的开关。本轮未修改这些设置。通信段 `NeedLogin=0` 等现值同样不是取消协议登录/权限步骤的依据。

安装树及现有索引中没有找到可直接使用的 Easy521 应用上传 CLI、通信 SDK 头或宿主示例。`ProgCompear.exe` 的文件过滤器和界面字符串指向 `.down/.updown` 比较；未验证其接受参数上传应用的契约。既有 InoLoaderPro 机型表不包含 Easy521，仍不能作为替代应用下载器。

## 新的离线研究靶标：独立 Windows 仿真器

这条线不依赖猜测 GUI 菜单，可从保留的符号和指令继续研究：

- `Simulator/h5u_daemon.exe` 的 `_main` 位于 `0x4081d0`；`getopt` 字符串 `dp:w:` 位于 `0x40a07a`。`-d` 调用 `create_daemon`，`-p` 经 `atoi` 传给线程启动，`-w` 调用 `setup_workplace`，默认工作目录为 `app\`。
- `Simulator/monitor.dll` 的 `_monitor` 位于 `0x663471d0`，默认端口常量 `0x328b` 即 **12939**；可由传入端口覆盖。`_monitor_tcp_server` 位于 `0x663474e4`，`0x663475ce` 把 `0x7f000001` 传入 `htonl` 后用于 `bind`，因此这段代码绑定 **127.0.0.1**。
- `_monitor_tcp_client` 先收 8 字节，再以帧偏移 0 的小端 16 位值确定完整长度，随后交给 `mp_input`。
- `Simulator/mp_core.dll` 的 `_mp_input` 位于 `0x6590b358`，核对长度，检查偏移 2 的版本字节、按偏移 3 的选择字节路由，正文从偏移 8 开始。还能找到 `mp_file_input`、`mp_index_read_normal_file`、`mp_index_verify_normal_file`、`mp_index_write_normal_file` 等符号。

这些是当前仿真器构建的静态事实，不构成完整协议定义。路由中同时有读、写和控制处理；没有被分类的请求不能当作只读。本轮没有启动仿真器或发送请求，也没有证明这些帧适用于真实 Easy521 的 USB 或网口。`ServerConfig.ini` 中的 OPC UA 回环地址属于另一个服务，不能混同此处 12939 的监控/文件协议。

下一轮可先沿仿真器命令表与文件映射做纯离线解析，并与 IH530 路径构造交叉核对；随后才考虑独立、隔离的仿真宿主验收。真机最短路径仍需拿到完整文件事务和独立目标/字节校验，而不是把内存参数读写作为任意应用编程的替代。

## 第二轮：静态命令表与 IH530 数值冲突

补充核对继续只读 `Simulator/mp_core.dll`，没有启动进程、加载 DLL 或连接 socket。`_mp_connect`（`0x659085c4`）把 selector `0x01` 注册到 `mp_index_input`、`0x03` 到内存、`0x04` 到文件、`0x05` 到 trace，另有 `0x00` scan、`0x80` login、`0x81` logout。TCP 连接初始化传入的标志为 `0x81`；是否登录及映射权限仍由各层检查，不能把连接建立视为已登录。

目前能静态描述的头部是：

| 相对完整帧偏移 | 长度 | 观察到的作用 |
|---|---|---|
| 0 | 2 | 小端总帧长 |
| 2 | 1 | 版本字段，解析器拒绝大于 2 的值；实际生产者的规范版本尚未核验 |
| 3 | 1 | 上述协议 selector |
| 4 | 4 | 本轮分发函数未解释；不能擅自赋予序号、连接或保留字段语义 |
| 8 | 可变 | 子协议正文 |

`mp_index_input` 位于 `0x65909d14`。以正文起点计：偏移 0 的操作值 `1` 调用 read、`2` 调用 write、`3` 调用 verify、`4` 调用另一种 write；偏移 2 是小端 32 位 index，偏移 6 是小端 32 位长度，write/verify 数据起点为偏移 10。偏移 1 的意义未确定。read 响应将正文操作改成 `0x05`，数据从正文偏移 2 开始；错误响应是 `0x0f` 加偏移 2 的 32 位错误值。这些约束来自具体分支，不是已通过连接验证的完整客户协议。

`_mp_index_cmd_mapping`（`0x65909064`）共有 **32 次静态注册**，完整列表已追加到 JSON。几个关键条目如下：

| 仿真器 index | read 回调 | write 回调 | 额外事实 |
|---|---|---|---|
| `0xff000000` | `mp_index_read_status_word` | `mp_index_write_control_word` | read 要求长度恰为 **1**，取 `g_dev_status_word`；RUN/STOP 位含义尚未核验 |
| `0xff000030` | `mp_index_read_system_init_status` | 无 | read 要求长度恰为 **1** |
| `0xff100000` | `mp_index_read_plc_version` | `mp_index_write_autoshop_version` | read 要求长度恰为 **240** |
| `0xff000010` | `mp_index_read_load_down_file_status` | `mp_index_write_load_down_file_control` | 两种方向不能混同 |
| `0xff010000` | `mp_index_ect_scan_read_status_word` | `mp_index_ect_scan_write_control_word` | 是 EtherCAT 扫描路径，不能作为 CPU 运行态索引 |

并行 IH530 调查在 `H530Communicate.dll` 的 `CRTUStateCommand` 执行函数观察到：把逻辑值 `0xff010000`、初始化为 4 的输出长度传给共同执行层 RVA `0xc6c0`，再回填首 DWORD。**这里不能相互印证，反而存在明确的层级冲突。** 仿真器相同数值表示 EtherCAT，CPU 状态候选的长度也不同。尚缺共同执行层如何组织、变换该逻辑值，以及真实仿真客户端如何生成帧；不能直接把逻辑常量搬进仿真器 index 字段。IH530 细节参照[并行报告](12-interface-control-2026-10-18.md)。

文件路径也有两种不同入口：

1. index 文件映射把 `0x00011000–0x000110ff` 绑定到 `default\alias.foid`，含 read、alias write、verify 回调；`0x00028000–0x000280ff` 绑定到 `varsinfo.foid`，没有 write 回调。read/verify 最终调用 `foid_open/read/compare`，因此这些是 FOID 索引访问，不能当作任意 ELF 字节偏移读取。
2. selector `0x04` 的 `mp_file_input` 使用另一张跳转表：操作 `1/2` 进入 route create/open，`5` 写、`6` 读、`15` 销毁路由。create 正文偏移 18 是文件名，偏移 2 进入密码检查；偏移 10/14 的字段尚未完整定型。操作 `2` 的已存在路由分支可调用 `ftruncate`（`0x659077fc`），故“先打开文件”也不能笼统算只读。

`_file_route_path`（`0x65907df4`）把 `/` 改成 `\`，把前缀 `\am600-nand\` 换成 `.\`，移除 `PrgBackup\` 前缀，并对其他以 `\` 开始的路径移除首分隔符。这是 Windows 仿真工作目录的路径适配证据；不是设备文件系统访问证明，也不是建议尝试任意路径。

最短的下一本地实验靶标已缩小为：隔离仿真工作副本后，仅允许 selector `1`、read 操作 `1`、index `0xff000000`、长度 `1`，保留原始返回字节并独立校准含义。**本轮尚未实施这个实验。** 外层偏移 4–7、规范版本、登录/会话生命周期、状态字节语义及完整文件事务仍未闭合；因此没有交付可宣称正确运行的客户程序，更不把这一套推为 Easy521 USB 协议。
