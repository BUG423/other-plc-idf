# 完整应用事务：Linux 两级链接实测与工程变量符号注入

2026-10-19。范围为本机已安装 AutoShop 组件的静态分析，以及 Linux LLVM LLD 对现有 ARM 对象的离线链接。未执行厂商 EXE/DLL、访问 Windows 进程、GUI、网络设备、USB 或 PLC；未修改原工程。仅将对象副本保留在忽略目录 `.plcidf/`，仓库交付工具、测试和[实验元数据](16-application-transaction-2026-10-19.json)，不提交厂商二进制或完整反汇编。

**本轮实际打通了两个对象链接阶段，并把剩余差异定位到 8 个工程变量的符号地址/类型注入。** 新产物的代码节和重定位节与当前厂商产物逐字节相同，但 ELF 并不相同，也不是已验证可下装应用。下载侧恢复出了“生成有序文件表 → 事务状态 2 → 逐文件传输 → 事务状态 3”的具体调用链；其会话初始化和设备协议仍需独立验证。

## 1. 从厂商代码恢复的真实两级链接

链接命令来自 `H530Converter.dll`，不是只在安装树中发现了一个链接器文件。VA 为该哈希 PE 镜像的首选地址，不是运行时地址。

| 阶段 | 输入顺序 | 输出 | 关键调用地址 |
|---|---|---|---|
| 1 | 显式 POU 对象列表，最后 `_SysLib.o` | `Temp/stuserprog.o` | `0x100f6869` |
| 2 | 仅 `Temp/stuserprog.o`，再 `_STCLib.o` | `Temp/stprog.o` | `0x100f6b1a` |

协调函数始于 `0x100f61a0`。两次都调用 `0x100f6cf0`；命令模板位于 `0x101b8f10`，为 `-relocatable %s -o "%s"`，使用安装目录 `Bin/ld.lld.exe`。原实现通过 `ShellExecuteExW`、等待和退出码检查执行，并另调用一个尚未完整解析的输出校验函数 `0x10135cd0`。

ARM 模式选择 `STTemp` 和 `ARM/`；仿真分支选择 `STSimTemp` 和 `X86/`。库分支由 `CSTCompiler` 单例虚表 `+0x138` 的 WORD 返回值 bit 0 决定：0 为 `SysLib`，非 0 为 `SysLib_New`。导出中 `GetCompilationOpt` 与此签名相符；本轮没有读取运行中编译器的选项值，不把该候选方法名当作已读取的工程配置。

POU 列表累积达到 30,000 字符时，厂商代码先生成 `stprogTemp%d.o`。新增工具只覆盖本轮的小工程路径，超过有界范围直接拒绝，不擅自重排或实现另一套分块算法。原厂 helper 等待 10 秒并存在最多 5 次尝试；新工具每个命令只执行一次，超时保留失败证据。

## 2. Linux 真正执行了什么

旧的 `/tmp` 工具目录已不存在。本轮按统筹授权下载并仅解包 Ubuntu 官方 `lld-20`，未 sudo 安装、未改系统路径：

- 包版本：`1:20.1.2-0ubuntu1~24.04.3`，依赖本机已安装的同版本 `libllvm20`。
- 包 SHA-256：`50d8db763d3e3526c050d01c2c8c9c94e3f94cca6f7dbdb8f9a4c65aefefcf83`，与 `apt-cache show` 元数据核对一致。
- 链接器版本读回：`Ubuntu LLD 20.1.2 (compatible with GNU linkers)`。
- 链接器 ELF SHA-256：`5c8dc44318df21528d0456cae914061cdc30221e40445b8cef84ba57f352445b`。

`Bin/ld.lld.exe` 仅静态读取，包含 `llvm-mc (based on LLVM 10.0.0)` 字符串；这不是执行获得的厂商链接器版本，也不能证明它未被修改。

在全新 `.plcidf/research-16-link-20261019T014840/` 中冻结了同一次读取的 `STTemp/MAIN.o`、`STTemp/SBR_001.o`、两个库分支和当前 `Temp` 参考产物。所有输入副本只读。按 `MAIN.o → SBR_001.o → _SysLib.o` 的显式顺序，两分支的两级命令均 exit 0。`_SysLib.o` 在两分支字节相同，差异在 `_STCLib.o`。

| 对象 | 厂商当前参考长度 | Linux `SysLib_New` 长度 | UND 唯一名称 | 重定位条目 |
|---|---:|---:|---:|---:|
| `stuserprog.o` | 6,240 | 7,084 | 两者均 13 | 两者均 125 |
| `stprog.o` | 127,464 | 146,492 | 两者均 23 | 两者均 3,906 |

旧 `SysLib` 的最终对象只有 3,781 个重定位条目，因此不能与新分支混用。新分支与当前参考更吻合，这是冻结输入上的对照结果，并不等于从工程中读到了库选择 bit。

对两个新分支产物分别逐节比较，以下内容全部**字节相同**：`.text`、`.ARM.exidx`、`.debug_frame`、`.ARM.attributes`、`.rel.text`、`.rel.ARM.exidx`、`.rel.debug_frame`。最终 `.text` 为 63,864 字节；`.rel.text` 为 29,520 字节。BSS 是 `SHT_NOBITS`，没有对应文件字节；只比较内存长度，两个最终产物均为 13,936 字节。

文件差异集中于 `.strtab` 和 `.symtab`。LLD 20 的字符串表更长；本轮另试 `-O1/-O2` 均不改变输出哈希，这两个选项不在恢复出的厂商参数中，工具也不加入。字符串表差异之外还有下节的 8 个实际语义差异，不能把全部差异归因于链接器版本或布局。

随后用新增工具本身在另一全新目录 `.plcidf/research-16-tool-link-new-20261019/` 重跑，两个输出哈希与首次实验完全相同：

- `stuserprog.o`：`001ac7470ddbc3db378d554eb1c05016942bf333fa11f5e0f4e23444f1e233ff`。
- `stprog.o`：`9ef1a87223b279b6a90b722beb6b86b6067b6255eee1fb23fd734adfe9b78b92`。

## 3. 具体缺口：8 个全局变量仍缺地址/类型注入

将 `.symtab` 每项的字符串偏移解析为名称，再比较 `st_value/st_size/st_info/st_other/st_shndx`，两个阶段都精确剩下 8 项差异。Linux 新产物中它们的 `st_info=0x10`、`st_value=0`、`st_shndx=0`；当前厂商参考为 `st_info=0x18`、下表地址、`st_shndx=0`。第一阶段其余 129 项、第二阶段其余 1,668 项规范化后相同。

| 冻结样本中的符号名 | 厂商参考 `st_value` |
|---|---|
| `G_IBESS_MODE` | `0x2100130` |
| `G_RBESS_CONFIDENCE` | `0x21001c0` |
| `G_RBESS_INFLUENCEP` | `0x2100180` |
| `G_RBESS_INFLUENCEQ` | `0x21001a0` |
| `G_RBESS_PCMD` | `0x21000f0` |
| `G_RBESS_QCMD` | `0x2100110` |
| `G_RBESS_SENSVP` | `0x2100140` |
| `G_RBESS_SENSVQ` | `0x2100160` |

**这些地址只是当前参考产物的观察值，工具不复制、不注入，也不把它们用作独立构建的配置。** `st_shndx` 仍为 UND 并不是遗漏了解析；这里的低 4 位类型 8 已超出常见标准对象类型用途，必须尊重厂商运行时含义。

静态追踪进一步固定了后处理的来源和写入规则：

1. `H530Converter.dll` 的 `0x1016d5d0…0x1016dc40` 构造 `SInfo` 数组。记录步长 **12 字节**：`+0` 是窄字符名称指针，`+4` 是 DWORD 地址，`+8` 是类型字节。普通全局变量记录在 `0x1016dacc` 写入类型常量 **8**。
2. 地址解析 helper `0x1016d090` 的普通变量分支调用 `IH530SysResMgr::GetVarByName`（`0x1016d1f1`），经 `CH530Var` 的子接口虚表访问地址和类型。带点号/十进制后缀及操作数还有 `GetVarAddrByName`、`GetOperInfo` 分支，不能一概按普通 DWORD 变量处理。
3. 名称在 `0x1016d95c` 检查 `::` 前缀，存在去前 2 字符的分支，再于 `0x1016d9d7` 调用 `CSTCompiler` 虚表 `+0x3c` 的名称映射。当前 ELF 名称确实大写，但这个映射尚未完整解码；不能把所有 Unicode、命名空间和实例变量都简化为 `.upper()`。
4. 构造数组后，于 `0x1016dbb2`、`0x1016dbd1` 分别对 `Temp/stprog.o` 与 `Temp/stuserprog.o` 调用 `STRelocation::Relocation(wchar_t const*, SInfo const*, unsigned int, unsigned long&)`。
5. `STRelocation.dll` 的 `0x10003b80` 处理符号表；`0x10003d09…0x10003d1c` 对匹配项执行 `st_info = (old_st_info & 0xf0) | SInfo.type`，并将 `SInfo.address` 写入 `st_value`，本段不改 `st_shndx`。这与 8 项实际差异吻合。名称/索引匹配和关联重定位过滤仍有未完整复现的部分。

下一阶段可以先固定已有合法全局变量/任务接口，研究如何从经过校验的项目变量记录生成 `SInfo`，再与厂商输出对照。完整变量分配器、类型规则、名称映射和任务接口未验证之前，不能通过抄参考地址或手工改符号来声称“Linux 完整应用构建已通过”。`变量表.gvt`、`VarsInfo.vi`、`varsinfo.foid` 目前只是需要核验的关联来源，未当作已解析地址合同。

## 4. 当前下载路径是有序记录表和独立事务状态

31 个 `DownloadFile` 调用点并非 31 个应用文件，还包含系统升级、Firewall 和 GL20 路径。旧 `CH530ProgressDlg::DownLoad`（`0xe63e90`）虽然保留完整文件逻辑，但本轮未找到它的直接 E8 或绝对指针调用者，因此不将该旧函数独自当作当前 Easy521 路径。

本轮恢复出更强的调用链：分发值 `0x5d` → `0xe6b470` → `0xe79a60` → `0xe7a170`，后者日志名为 `CH530ProgressDlg::CaculateAllDownloadFile2`。它先查询 PLC 信息，按型号、版本、POU 和功能分支生成有序 vector，再进入传输阶段。

vector 每项 **12 字节**：消息 ID（`+0`）、本地路径 `CStringW`（`+4`）、远端路径 `CStringW`（`+8`）。构造器 `0xe6b140` 和追加函数 `0xe80f40` 可直接核对。传输循环 `0xe6b250` 按原顺序消费，在 `0xe6b380` 实际调用：

```text
IH530Communicate::DownloadFile(
    record.local_path, record.remote_path,
    progress_window, empty_CStringW,
    EnumExecuteMode(raw=1), EnumCommandLevel(raw=0))
```

每项返回值必须恰为 **1**；失败直接返回，不在此循环自动重放。消息 ID 用于进度和错误呈现，不是文件协议 index 或运行时地址。

在正常循环前，`0xe63b20` 经 IAT `0x1eb1730` 调用 `SetGCMState`，传入枚举指针初值 **2**、空 `CWnd*`、mode 1、level 0。循环返回 1 后，再由 `0xe63b90` 同接口传入枚举初值 **3**，也要求返回 1；例如 `0xe79d61 → 0xe79d7a → 0xe79d8e` 明确保持此顺序。**这里仅固定状态值和次序，不擅自给 2/3 命名为“停止/启动”或“提交”。** 故障退出是否需要回滚、连接重建和状态恢复，仍须解开接口实现与设备结果。

## 5. 实际文件表与生成字节

以下为 `CaculateAllDownloadFile2` 的静态相对顺序；条件分支并非总会加入记录，不能把表无条件全部发给 Easy521。完整消息 ID、追加地址、生成函数地址见 JSON。

| 本地来源/生成入口 | 远端名称 |
|---|---|
| `0xe71ce0` 准备的工程归档，存在 `.hcpp` / `Temp/Empty.hcpp` 分支 | `project.hcpp`，部分路由加前缀 |
| `Temp/userprog.foid`，存在 `0xe70570` 变换分支 | `userprog.foid` |
| `Temp/stprog.o` | `stprog.o` |
| `Temp/stuserprog.o` | `stuserprog.o` |
| `Temp/symbol.sym` | `symbol.sym` |
| `Temp/varsinfo.foid`，不是工程根目录同名文件 | `varsinfo.foid` |
| `Output.sdt` → 生成 payload → FOID writer | `syscfg.foid` |
| `EtherCat.dat` → 生成 payload → FOID writer | `ethercat.foid` |
| `TcpModbusConfig.cfg` → 生成 payload → FOID writer | `modbus.foid` |
| `canopen.data` / `CANLink.prg` 分支 → 生成 payload | `canlink.foid` |
| `h5u_moduleCfg.data` → 生成 payload；另有 GL20 分支 | `modcfg.foid`；不把 GL20 名称推为相同 |
| `EIP.dat` → 生成 payload | `ethip.foid` |
| `EIPIns.dat` → 生成 payload | `EIPIns.foid` |
| `ecttrans.foid` → 生成 payload | `ecttrans.foid` |
| `dataLog.data` → 生成 payload | `dataLog.foid` |
| `webserver.bin` → 生成 payload，版本/功能可选 | `webserver.foid` |
| `OpcUAServer.dat` → 生成 payload | `OpcUAServer.foid` |
| `SFTPServer.dat` → 生成 payload | `sftpserver.foid` |
| `Ecam.tr` → 生成 payload | `Ecam.foid` |
| `autosys.cfg`，含 `EASY51X-AG` 型号分支 | `autosys.cfg`，不推为 Easy521 必需项 |

历史 USB 成功日志的 18 个名称是 UI 标签，其中 `EIP.dat` 和 `OpcUAServer.dat` 不能直接用作远端参数。新表与历史日志可以关联，但日志没有传输字节哈希，不能反推出历史 USB 字节，更不能用当前 `PLC/`、`Temp/`、`OnlineVersion/` 的同名文件拼成一套已验证应用。

生成配置使用 `0x15b2a90`：先调用 `0x15b2dd0` 生成 **64 字节头**，再追加各生成器提供的 payload，写入 `project_root\Temp\DownLoad\<远端名称>`。路径由对象构造器 `0xe62660…0xe62723`、字符串资源 `0xf35c`（中文资源值 `\Temp`）和 `\DownLoad\` 字面量交叉确认。

64 字节头当前可固定以下局部结构：LE DWORD `(0x100,4)` 后是 4 字节 `foid`，再是 LE DWORD `(0x200,32)` 和 32 字节尚未解释的元数据，最后 LE DWORD `(0x201,4)` 与调用方提供的 DWORD。静态内存复制及现有暂存文件前缀相符。**中间 32 字节不能猜成零、时间或校验和，最后 DWORD 的版本策略也未完整确认。** 配置 payload 有各自生成器，不能把原始 `.dat/.cfg` 加一个通用头就冒充完整包。

另一条分发 `0x125/0x126` 路径调用 `0xe73060`，传输 `PLCProgram.down/.updown`，再执行 raw simple command 0、轮询 raw 123。它不是本轮证明的标准文件循环尾部，不能把这两个值直接套作上述事务提交命令。独立程序校验、备份可恢复性和设备回读仍分别验收。

## 6. 可复用工具与验证

新增 [link_vendor_application.py](../../../tools/link_vendor_application.py) 提供：

```python
link_application(*, pous, syslib, stclib, lld, output_dir, timeout_s=10.0, runner=...)
```

输入必须为显式有序 POU、两个明确的库和受信任本机 Linux LLD；拒绝非 ELF32 little-endian ARM `ET_REL`、重复路径、最终 symlink、现存输出目录、输入目录下输出和超限输入。逐个消耗总读取预算，避免先分配所有文件后才检查上限。冻结副本只读后才传给 LLD，使用参数数组、`stdin=DEVNULL`、明确超时、无 shell、无重试。末尾复核原输入、副本、链接器及两个输出哈希。

新目录中的 `link-report.json` 包含工具版本/哈希、原输入与冻结副本哈希、逐次 argv/退出码、两个产物哈希及 ELF 的未解析符号和重定位。成功仅返回 `ok=true,status=linked,scope=two-stage-relocatable-object-link`，始终 `downloadable=false,st_compiled=false,runtime_verified=false`；已发起链接后的失败保留记录与诊断，不覆盖旧证据。它不实现上节的 SInfo 注入，也不提供设备操作。

复现本轮冻结样本：

```bash
.venv/bin/python tools/link_vendor_application.py \
  --pou .plcidf/research-16-link-20261019T014840/inputs/STTemp/MAIN.o \
  --pou .plcidf/research-16-link-20261019T014840/inputs/STTemp/SBR_001.o \
  --syslib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_SysLib.o \
  --stclib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_STCLib.o \
  --lld .plcidf/research-tools/lld20/extracted/usr/lib/llvm-20/bin/ld.lld \
  --output-dir .plcidf/another-new-private-link-directory
```

`output-dir` 必须全新；输入副本和输出都是私有厂商对象，只分享元数据。保留 `ld.lld` 的符号链接名称调用，因为 Linux LLVM multicall 二进制依赖 `argv[0]` 选择 ELF 链接模式。

专属 **14 项测试全部通过**：包括真实 Linux LLD 对合成 ARM ELF 的两级正向链接、真实重复强符号的链接失败，以及 mock 的超时不重放、第二阶段失败、不合法输出、原输入/副本/工具/中间产物漂移、累计内存预算等负向测试。命令如下，不访问 Windows 或设备：

```bash
PLCIDF_TEST_LLD="$PWD/.plcidf/research-tools/lld20/extracted/usr/lib/llvm-20/bin/ld.lld" \
  .venv/bin/python -m unittest discover -s tools/tests -p test_link_vendor_application.py -v
```

研究已把“有 ELF 对象但不知道怎么链”推进为可重复的两级链接，也把剩余 ABI 差异收敛到具体工程变量后处理。下一项应是合法变量分配记录、名称与类型映射，再连接完整配置生成和已初始化的文件事务；当前没有新增完整 ST 编译、无 GUI 下装或 CPU 执行通过声明。
