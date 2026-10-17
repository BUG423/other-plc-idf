# 路线 1：保留厂商 runtime 的离线应用检查工具

日期：2026-10-17。本轮新增可复用只读工具 [inspect_vendor_application.py](../../../tools/inspect_vendor_application.py)，并用实际工程、归档工程和 Linux LLD 输出做了 12 文件、6 组差分。[真实输入报告](07-vendor-application-inspection-20261017.json)保存哈希与结构摘要；没有保存厂商二进制、代码段、完整源正文或接口文档。

**已实现的是 Linux 离线检查和差分，尚未实现保留 Easy521 runtime 的原生 ST 编译、应用包装或下载。** 路线 1 的研究不阻塞优先推进的路线 2。

## 交付工具与接口

工具只使用 Python 标准库，不执行子进程、厂商 EXE/DLL，不依赖 Windows、readelf、USB 或 PLC。输入以只读方式打开；指定 `--output` 时只写报告，并拒绝报告路径覆盖任何输入。默认单文件上限 32 MiB、所有输入合计 128 MiB、最多 64 个不同文件；符号/重定位数量与字符串长度也有边界。

```bash
python3 tools/inspect_vendor_application.py MAIN.o Temp/stprog.o Output.prg Output.sdt

python3 tools/inspect_vendor_application.py \
  --compare /tmp/fixture-A/MAIN.o /tmp/fixture-B/MAIN.o \
  --compare /tmp/fixture-A/Output.sdt /tmp/fixture-B/Output.sdt \
  --output /tmp/plcidf-application-diff.json
```

`--compare BASE CANDIDATE` 可重复，比较双方会自动纳入输入。默认 stdout 是 JSON；退出码 0 表示所有输入已完成检查，1 表示至少一个输入不可读或标准 ELF 结构有错误，2 表示参数/报告写入失败。**未知厂商格式可以被成功观察，因此退出 0 不表示格式已解码或应用可部署。**

Python API 为 `inspect_paths(paths, comparisons=[(base, candidate), ...], max_file_bytes=...)`，另有 `inspect_bytes(data)` 和 `parse_elf(data)`。输出包含：

- 完整文件 SHA-256、长度、最多 16 字节且小于文件长度的前缀、可见 ELF 魔数偏移；不把完整小二进制伪装成头部十六进制写入报告。
- ELF32/64、大小端、机器类型、ET_REL/EXEC/DYN 类型、ARM EABI 标记、节表、GLOBAL/WEAK 未解析和已定义名称、REL/RELA 计数及类型。
- `.ARM.attributes` 的长度与 SHA-256。当前保留属性指纹，不声称已经解码其全部 ABI 属性；CPU 特性的人读证据仍可参照前一轮 readelf 记录。
- 文件级差分：哈希、长度变化、首个变化偏移、重叠部分不同字节数；两个 ELF 还比较架构、标记、重定位数量及符号名称增减。
- 文件关系：逐字相同的内容组、仅文件名主体相同的分组，以及消费者未解析名称与提供者已定义名称的交集。名称交集明确标记 `abi_compatibility_confirmed=false`。

对厂商文件只命名“观察到的前缀”：`AutoShop\0`、`****AS*****\0`、`SA\0\1`。工具不把这些字节解释为版本、压缩、加密、签名或可下载容器。头部 4 字节数值与文件总长度的相等关系也只记录为算术观察，`field_semantics_confirmed=false`。

## 真实对照结果

两组工程分别是仓库归档 `plc/easy521/autoshop/BESS_E521_DEV`，以及 Windows 文件系统中实际活动工作副本 `E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV`。只通过 Linux 文件系统读取文件，没有访问 Windows 进程。它们是两个现存样本，不是严格的一次单变量源码实验，不能把差异归因于某一条 ST 语句。

| 文件 | 归档字节数 | 工作副本字节数 | 哈希相同 | 主要可确认观察 |
|---|---:|---:|---|---|
| `STTemp/MAIN.o` | 34,284 | 4,472 | 否 | ARM ELF；未解析唯一名称 82 → 36；重定位 1,618 → 84 |
| `Temp/stprog.o` | 155,376 | 159,012 | 否 | ARM ELF；未解析唯一名称 83 → 82；重定位 5,316 → 5,497 |
| `STTemp/MAIN.st` | 12,839 | 3,460 | 否 | 两者都以 `****AS*****\0` 开头，是带不透明前缀的数据，不是可直接按 UTF-8 正文同步的纯文本 |
| `Output.prg` | 56 | 56 | **是** | 本样本前 32 字节均为 `FF`，没有可见 ELF 魔数；工具保留 unknown 格式 |
| `Output.sdt` | 182 | 182 | **是** | 前缀 `SA\0\1`；偏移 20 的 4 字节按大端读取为 182，恰等于文件长度 |

`Output.prg` 两份 SHA-256 均为 `3bfe2324eba14f55199d5bb4d0ee72d265a7453fc78b0c6e66d265ae21adb63b`；`Output.sdt` 均为 `c07a72afae6f4cac3e87308bab17d0eec1658a764b7e67102de77ec984ca863b`。虽然 MAIN 与组合对象不同，这两个小文件逐字相同。**因此不能仅靠 Output.prg/sdt 的存在或哈希绑定本次 ST 逻辑，也不能据文件名断言其包含完整 ARM 程序。** 它们的实际角色与其他文件/下载协议之间的关系仍待单变量样本验证。

工作副本 MAIN.o 的 36 个未解析名称与 stprog.o 的 82 个未解析名称，与 [Linux LLD 实验](06-linux-link-experiment-2026-10-17.md)一致。这里归档 stprog 的 83 和工作副本的 82 是不同输入，不是条目去重造成的差异；工具同时记录条目数和唯一名称数。

另将前轮 LLD 成功组 E（MAIN＋旧 `_STCLib.o`＋`_SysLib.o`）与工作副本 stprog.o 比较：LLD 输出为 143,824 字节、46 个未解析名称、3,778 条重定位；比厂商 stprog 少 15,188 字节，且已定义/未解析名称集合都不同。Linux 部分链接产物仍不是完整厂商组合对象的替代品。

工作副本 `MAIN.rst` 恰为成功标记。工具把它识别为 `compiler-success-marker`，并标记 `marker_does_not_prove_build=true`；没有把单个旧标记升级为本次编译成功证据。

## 验证

纯离线测试：

```bash
.venv/bin/python -m pytest tools/tests/test_vendor_application_inspection.py -q
```

14 项测试通过，包括 ELF32/64 与大小端、每一个截断位置、越界节表/符号名称、无效重定位索引、零记录长度、扩展节数量、未知与空输入、厂商不透明前缀、多输入差分、文件上限、缺失文件、JSON 输出及输入覆盖拒绝。

对真实报告中的 5 份 ELF，另用已安装的 GNU readelf 2.42 独立比较 GLOBAL/WEAK 未解析条目/名称和 ARM 重定位各类型/总数，全部一致。这一步是验证工具的本地实验，工具本身不依赖 readelf。交叉校验记录位于 JSON 的 `independent_validation`；工具源码 SHA-256 也已固定在报告中。

## 下一步：最小 golden fixture 清单

以下是尚未执行的、可交给后续现场/工具链任务的清单。本轮没有创建、打开或修改 AutoShop 工程。

1. 单独建立可恢复的工作副本，只含 MAIN 和两个预先声明的 DINT 内存变量 `fixture_in`、`fixture_out`，不绑定物理输出。冻结型号、固件、AutoShop/编译器版本、任务周期、变量声明/初值/保持属性、库对象哈希与所有工程输入。所有变体共享这些固定项。
2. G0：正文 `fixture_out := fixture_in + 1;`。在没有源码修改的情况下再编译一次 G0-repeat，用来识别时间戳、随机量或 GUI 保存引起的差异。
3. G1：只把常量 1 改为 2。比较 native `MAIN.ST`、生成的 `STTemp/MAIN.st`、`MAIN.o` 的节/重定位/符号、`stprog.o`、`Output.*`，确认哪层确实随逻辑变化。
4. G2：只替换成一个条件分支，变量声明保持不变；G3：单独增加一个内存变量。分别识别指令流变化与变量 ABI 变化，不能一次同时改周期、型号和变量布局。
5. G-negative：只引入语法错 `fixture_out := ;`。编译前隔离旧必需产物，保存原始诊断与产物缺失事实，再恢复 G0。语法失败不得保留旧成功标记作为新成功证据。
6. 每次把原始输入/产物保存在本机独立目录；至少包含 native MAIN、工程/变量/任务文件、`STTemp/MAIN.{st,o,rst,cl}`、所有相关 POU 对象、`Temp/stprog.o` 和 `Output.{prg,sdt}`。保存编译日志、版本及哈希清单；仓库只提交来源、清单和脱敏结构差分，不提交厂商二进制。用本工具成对检查 G0/G0-repeat、G0/G1、G0/G2、G0/G3。

这组样本可验证偏移 20 的“大端值＝总长度”是否跨多个长度成立，并判断 Output.* 是配置描述、控制信息还是另有作用。在多样本对应关系得到证实前，不增加打包器或修改任何校验/签名字段。

## 下一步：只读协议取证接口清单

本轮没有抓包或触发设备交互。后续最小采集接口可约定为：`capture(session_id, interface, confirmed_peer, duration_s, output_pcapng)`；输入必须明确采集点、已核实的 PLC 身份/地址与时间上限，输出包含 pcapng SHA-256、采集起止时间、丢包统计、操作者动作时间线和工具版本。

- 先证明采集点能看到目标会话。WSL 内的网络采集不能直接假设可见 Windows AutoShop 的全部网口流量；USB 流量也需要实际宿主层的被动采集能力。不要为取样重新绑定 USB、重置设备或改变已有连接。
- 首组取样只记录已明确为只读的在线身份/状态或变量读取动作，同时保留同等时长的空闲基线。不要为了研究协议自动触发下载、RUN/STOP、解锁或固件操作。
- 解析端先做纯离线统计：方向、连接、时间戳、消息边界、长度、重复请求和响应对应关系。字段含义必须能由已知动作与多组样本交叉验证；看见相似字节不等于识别到下载命令或完整性算法。
- 若只有应用下载时才出现某个必需包装/握手，明确列为后续独立联调的输入条件。本轮不构造替代握手，不猜签名，不把未采到数据写成协议不存在。

路线 1 当前仍缺 LiteST 前端/代码生成、完整变量/POU/任务 ABI、组合对象规则、runtime 导入与重定位规则、真实应用容器以及下载/校验状态机。这些缺口可用上述样本逐项缩小，不由本工具的退出 0 或前轮 `ld.lld -r` 成功替代。
