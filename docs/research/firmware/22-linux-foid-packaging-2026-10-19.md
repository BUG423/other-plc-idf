# Linux 配置封装研究：FOID 头字段来源与严格离线检查

2026-10-19。本轮在 Linux 中静态读取已有 AutoShop 组件和既有 Easy521 工程副本，新增配置封装检查工具。没有执行 Windows 程序或厂商 DLL，没有访问 USB、网络设备或 PLC，没有修改原工程。厂商文件冻结于忽略目录 `.plcidf/research-22-foid-20261019/`；公开交付只包含工具、合成测试和[证据元数据](22-linux-foid-packaging-2026-10-19.json)。

**本轮确认了头部末 DWORD 的调用方来源，并把 32 字节字段追到空字符串转换后的缓冲区复制。Linux 现在可以严格检查这一封装及其与源配置的字节关系，但尚不能独立生成完整 Easy521 应用。** 本报告补充[第 16 轮记录](16-application-transaction-2026-10-19.md)，不改写历史结论。

## 1. 输入和研究范围

静态地址均为以下 PE 文件首选 VA，不是实际加载地址，也不是不同版本通用的接口地址。

| 组件 | SHA-256 |
|---|---|
| `AutoShop.exe` | `22efbe0e08e31f376fd83846ceaf66d9808386d75727facdf66da6242392031e` |
| `InterfaceMgr.dll` | `44409d9b93024652bb092debc632fd0946d3ecf1027deafad9753b3285b14192` |

冻结输入共 31 项：两组件、`BESS_E521_DEV/Temp/DownLoad/` 的 12 个配置文件、11 个关联源配置，以及 6 个关联工程/程序/符号文件。每项记录原路径、冻结路径、长度和 SHA-256，结束时重新核对原文件与副本均未改变。

**冻结时间接近不等于属于同一次构建。** 未建立这些现存缓存与同一份源码、编译选项、设备或下载事务的关联；它们不构成已验证的完整应用包，也不称为最小必需文件集。

## 2. 64 字节头和 32 字节字段的实际来源

`0x15b2a90` 将 `0x15b2dd0` 输出的 64 字节头与调用者提供的 payload 拼接写入暂存目录。其已观察结构如下：

| 文件偏移 | 内容 |
|---:|---|
| 0、4 | little-endian DWORD：`0x100`、`4` |
| 8 | 4 字节 ASCII `foid` |
| 12、16 | little-endian DWORD：`0x200`、`32` |
| 20～51 | 32 字节字段，本工具按不透明元数据处理 |
| 52、56 | little-endian DWORD：`0x201`、`4` |
| 60～63 | 调用者传入的 DWORD |
| 64 起 | 调用者提供的 payload，不由通用头生成器解析 |

中间字段现在有比“未知 32 字节”更明确的静态来源：

1. `0x15b2fe0` 压入 `.rdata` 中 `0x1eecfac` 的空 Unicode 字符串，构造临时 `CStringW`。
2. `0x15b2ffa` 经 IAT `0x1eb179c` 调用 `InterfaceMgr!EncodingManager::ConvertFromUnicode_ACPCStr`。该导出的实现地址为 `0x1005e550`。
3. `0x15b300e` 经 IAT `0x1eb1698` 获取 `HString` 的窄字符缓冲区指针。`InterfaceMgr` 对应实现 `0x1005ab00` 直接返回对象首个指针字段。
4. `0x15b301c` 从该指针固定复制头记录声明的 **32 字节**。

这条生成路径没有 payload 参数，所检查的指令中也没有把 payload 哈希、时间或项目 UUID 放入该字段的运算。因此不把字段命名为校验和、签名、时间戳或 UUID。**空字符串不代表这 32 字节必然全零**：字符串终止符后的缓冲区内容、初始化范围及设备对它的解释尚未确认，本轮不模拟其内存分配，也不声称已复现全部字节。

实际 12 个配置文件的该字段相同，其 SHA-256 均为 `53f4d0899cf3d3a58777ac26d6a039c98e421e05dff499ce1031f54edc0d9f55`。这些文件具有不同 payload，因而相同字段不能作为本轮的 payload 完整性证据。不能据此推导设备忽略该字段，更不能用全零替换后声称包有效。

## 3. 末 DWORD 来源：常量或模块默认版本

头生成器在 `0x15b310c` 复制其栈参数 `[ebp+0x10]`。文件表协调器准备该参数的实际来源如下：

| 远端文件名 | 传给封装器的末 DWORD 来源 | 本次文件中的值 |
|---|---|---:|
| `syscfg.foid` | 常量 0 | 0 |
| `ethercat.foid` | `g_GetModuleDefaultVer(0x30)` | 0 |
| `modbus.foid` | `g_GetModuleDefaultVer(0x60)` | 0 |
| `canlink.foid` | `g_GetModuleDefaultVer(0x50)` | 0 |
| `modcfg.foid` | 常量 0；另一个型号分支也传 0 | 0 |
| `ethip.foid` | `g_GetModuleDefaultVer(0x80)` | 0 |
| `EIPIns.foid` | `g_GetModuleDefaultVer(0xb0)` | 0 |
| `ecttrans.foid` | `g_GetModuleDefaultVer(0x90)` | 1 |
| `dataLog.foid` | 常量 0 | 0 |
| `webserver.foid` | `g_GetModuleDefaultVer(0xd0)` | 本次暂存目录无此文件 |
| `OpcUAServer.foid` | `g_GetModuleDefaultVer(0xa0)` | 1 |
| `sftpserver.foid` | `g_GetModuleDefaultVer(0xc0)` | 1 |
| `Ecam.foid` | 常量 0 | 0 |

IAT `0x1eb0aa4` 的导入名明确为 `InterfaceMgr!g_GetModuleDefaultVer(unsigned short)`。其导出 `0x1004ed80` 按 WORD 键查询进程中的模块表，找到时返回节点 `+0x10` 的 DWORD，未找到返回 0。各参数准备和封装调用 VA 记录于 JSON。

这证明了当前调用点的**来源和模块标识**，没有证明模块表初始化规则、其他软件版本的默认值、Easy521 固件兼容判断或设备接受条件。解析工具只报告 `caller_dword` 原始值，不能根据文件名把任意现存头认证为这一调用点的产物。

## 4. 配置 payload 与已有源文件的实际关系

对每个暂存文件剥除已解析的 64 字节头，再与明确指定的冻结源文件比较。结果不是一种通用的“原文件加头”规则：

| 暂存文件 | 关联源文件 | 源长度／payload 长度，字节 | 实际关系 |
|---|---|---:|---|
| `syscfg.foid` | `Output.sdt` | 182／725 | 不相同，也非源前缀 |
| `ethercat.foid` | `EtherCat.dat` | 243／206 | payload 是源前缀 |
| `modbus.foid` | `TcpModbusConfig.cfg` | 8／9 | 不相同，也非源前缀 |
| `canlink.foid` | `CANLink.prg`／`canopen.data` | 未取得源／8 | 无法比较 |
| `modcfg.foid` | `h5u_moduleCfg.data` | 225／515 | 不相同，也非源前缀 |
| `ethip.foid` | `EIP.dat` | 340／237 | payload 是源前缀 |
| `EIPIns.foid` | `EIPIns.dat` | 24／24 | 完全相同 |
| `ecttrans.foid` | 工程根目录 `ecttrans.foid` | 9／9 | 完全相同；根目录文件没有这一 64 字节头 |
| `dataLog.foid` | `dataLog.data` | 197／197 | 完全相同 |
| `OpcUAServer.foid` | `OpcUAServer.dat` | 307／33 | payload 是源前缀 |
| `sftpserver.foid` | `SFTPServer.dat` | 171／82 | payload 是源前缀 |
| `Ecam.foid` | `Ecam.tr` | 54／54 | 完全相同 |

例如 EtherCAT helper `0x15b31a0` 从文件读取数据，但读取长度取自内存中对象接口 `0xfd4120` 的返回值，并非简单使用文件总长。**206 字节只是本次样本值，不能硬编码成所有 EtherCAT 配置的长度。**其他“完全相同”或“前缀相同”的结果也只约束这些输入，没有实现任意配置的合法生成。

关联的 `Temp/userprog.foid` 长度为 294 字节，也符合相同的头布局，payload 为 230 字节，但其中 32 字节字段为全零，与上述配置组不同。检查器仅报告结构；它不据此验证程序内容、生成器来源或与 ST 对象的关联。`Temp/varsinfo.foid` 和 `symbol.sym` 使用其他结构，不能统一套这个头。

第 16 轮恢复出的完整文件表还有工程归档、用户程序、两个 ST 对象、符号和变量文件，并有型号、版本及功能分支。本轮没有证明全部分支对 Easy521 的取值，12 个配置的存在不代表它们全部必需，也不代表文件闭包已齐备。

## 5. 新增工具和验收

[inspect_easy521_foid.py](../../../tools/inspect_easy521_foid.py) 提供 `inspect_foid(data)` 和 `inspect_paths(paths, source_pairs=...)`：

- 只接受上述固定 ID、长度、magic 和偏移；截断、异序、未知布局明确报错，不猜通用 TLV 语法。
- 输出头、元数据和 payload 的哈希、长度及原始 DWORD，不输出完整 payload 或厂商二进制。
- 显式比较 FOID payload 与指定源文件的相等/前缀关系；同名文件不自动关联，也不推断生成方式。
- 限制最多 64 个输入、单文件 64 MiB、累计 128 MiB；检查读前后身份和修改时间，拒绝最终 symlink、目录、FIFO 及超限文件。
- CLI 报告只能写新文件，拒绝覆盖输入或已有报告。无子进程、无设备句柄、无 Windows 接口。
- 结构检查通过始终保留 `complete_application=false`、`downloadable=false`、`signature_verified=false`、`writer_origin_verified=false` 和 payload 语义未验证状态。

使用已经冻结的本次样本：

```bash
.venv/bin/python tools/inspect_easy521_foid.py \
  .plcidf/research-22-foid-20261019/project/Temp/DownLoad/*.foid \
  --compare-source \
    .plcidf/research-22-foid-20261019/project/Temp/DownLoad/ethercat.foid \
    .plcidf/research-22-foid-20261019/project/EtherCat.dat
```

真实离线检查已解析全部 12 个配置，并记录 11 对源文件比较，结果见 JSON；没有设备执行或接受证明。

[专属测试](../../../tools/tests/test_inspect_easy521_foid.py) **16 项通过**，包括全部短头长度、错误标签/长度/magic/字节序、不透明元数据变化、非样本 DWORD、空 payload、精确/前缀/不匹配关系、身份变化、FIFO/symlink、读取预算及报告覆盖拒绝。测试数据均在运行中合成，无厂商 fixture。

```bash
.venv/bin/python -m unittest discover -s tools/tests \
  -p test_inspect_easy521_foid.py -v
```

下一步需要独立确认配置内存对象如何序列化及决定有效长度、默认模块版本表如何初始化、各型号条件下的实际文件闭包，再把这些与新编译源码及应用身份关联。当前新增的是严格离线检查能力和字段来源证据，未新增应用生成或原生下载通过声明。
