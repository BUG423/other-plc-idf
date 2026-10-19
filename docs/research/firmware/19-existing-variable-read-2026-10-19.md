# 既有内存探针的变量读取候选（2026-10-19）

本轮将厂商 `ReadMemoryEx` 追到单变量请求构造：**selector=3、read operation=1，正文为 `count=1 / 原样符号地址 / 位数` 三个 LE DWORD，总帧长 22 字节**。它不是前轮 selector=1 的 FF index 读取，不能把现有 CPU 状态 codec/helper 直接套用。当前 +2 探针的 GVT/CSV 地址、声明位宽和源码均已重新读取并绑定哈希；没有访问 Windows、GUI、USB、socket 或 PLC，没有修改正在轮询的工具，也没有 Git 操作。[JSON 证据](19-existing-variable-read-2026-10-19.json)包含输入与选定代码范围的 SHA256。

这是一项有厂商主机代码支持的**最小只读实验候选**。变量响应的完整单项回显格式在厂商主机“去掉前 12 字节”的代码与独立 Simulator 代码中交叉观察到；其中响应 operation=2 的明确命名分支目前仅在 Simulator 恢复，未取得真实 Easy521 变量响应。未知之处没有提升为协议已验收。

## 既有变量与探针身份

读取目录是 `E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV`。`变量表.gvt` 的 SHA256 为 `63700c1f9b5a4b1201201442b1d614344d9b6346fd2ad913b2c22796560b95c5`，与 17 号报告冻结文件一致；独立 UTF-16 `VarNameAndAddr.csv` 为 `75e89e3c7ef94dcb53129f4e4b1b0ef4bf72947ac4296633276c0f0ac29e44fe`。使用现有有界 GVT 解析器重新解析，不从 ELF 填充地址。

| 名称 | 声明/位宽 | 既有 opaque 符号地址 | GVT 地址字段偏移 |
| --- | --- | --- | ---: |
| `g_iBESS_Mode` | INT / 16 bits | `0x02100130` | 1052 |
| `g_rBESS_SensVP` | REAL / 32 bits | `0x02100140` | 1113 |
| `g_rBESS_SensVQ` | REAL / 32 bits | `0x02100160` | 1181 |
| `g_rBESS_InfluenceP` | REAL / 32 bits | `0x02100180` | 1253 |
| `g_rBESS_InfluenceQ` | REAL / 32 bits | `0x021001a0` | 1323 |
| `g_rBESS_Confidence` | REAL / 32 bits | `0x021001c0` | 1393 |

地址保持厂商符号地址，不把 `0x02100130` 当成可解引用 RAM 指针、字节偏移或 Modbus 寄存器；没有除 8 或按推测重新分配。声明位宽与 17 号报告相同，并有下面的厂商调用点作为“长度按 bits 传入”的独立证据。

候选源码 [MAIN_plus2_body.st](../../../examples/easy521_native_probe/MAIN_plus2_body.st) 为 1060 字节，SHA256 `392ae08c3e1e2bbed430af326d287e042bf15d3dc8f674712a6ed748f40acc09`。它在扫描中写固定标志 `SensVP=52102.0`、`SensVQ=2.0`，Mode 每扫描加 1、Confidence 加 2.0，并在 Mode 范围/首扫描/标志不符时复位。该源码是用于解释未来读数的已知候选，不等于本轮证明 PLC 当前应用就是此源码；应关联既有下载/compare 记录及新读数。GVT/CSV 即使一致也可能相对设备陈旧。

## 厂商构造/RTTI/执行路径

以下为模块 RVA，两个 DLL 首选基址均 `0x10000000`：

1. InterfaceMgr `ReadMemoryEx` facade `0x68da0`，公开签名含两个 unsigned long、数据输出双指针、输出长度指针、CWnd、executeMode、commandLevel、EReadMemoryCommand。它转实现虚表槽 `+0xe4`。
2. H530 实现 `0x57c0` 分配 68 字节对象，于 `0x57f1` 写入虚表 **`0x7b304`**；RTTI 是 **`CReadMemoryCommandEx`**。
3. 配置函数 `0x1b3f0` 原样保存两个输入 DWORD 至对象 `+0x2c/+0x30`，另保存数据/长度输出地址。没有在此把地址转为字节数。
4. 该虚表真实执行槽 **`+0x3c = 0x1b470`**，再调用 **`0x1b4f0`**。该链与 18 号更正后的 GCM/RTU/EtherCAT 路径完全分开。
5. `0x1b53e–0x1b552` 分配 12 字节正文：`+0=1`、`+4=对象2c`、`+8=对象30`。`0x1b555–0x1b560` 调 protocol `+0x2c`，参数为 selector **3**、operation **1**、正文长 **12**、单帧模式 **1**，进入此前恢复的公共帧构造 `0x28050`。

厂商 AutoShop 两个独立调用点 `VA 0x008984b1`、`0x009f1aa1` 在此前调用 `IH530SysResMgr::GetVarAddrByName`（IAT `0x01eb148c`）取得符号地址，把它原样传给 ReadMemoryEx；第二参数为 `0x20`。通信成功后分别在 `0x008984bf`、`0x009f1aaf` 起只复制 **4 字节**数据。这支持 **32 bits 请求 → 4 bytes 数据**，不是把长度 32 当 32 字节。另一 UI 上下文对数组长度有专用移位，不能外推到本轮标量。

## 合成的固定单项请求

公共 header 与前轮相同，但 selector 与正文不同：总长 LE16@0=22、version@2=1、selector@3=3、sequence LE16@4、保留@6..7=0、operation@8=1、普通 flag@9=0；正文 count LE32@10=1、address LE32@14、bit length LE32@18。

下表 sequence 固定为 `0x1234` **仅作合成展示，均未发送**；真实独立实验仍应生成新的 u16 sequence，不用它证明认证或绝对鲜度。

| 固定变量 | 合成请求 hex |
| --- | --- |
| Mode / 16 bits | `16000103341200000100010000003001100210000000` |
| SensVP / 32 bits | `16000103341200000100010000004001100220000000` |
| Confidence / 32 bits | `1600010334120000010001000000c001100220000000` |

没有任意地址扫描或 FF 命令猜测。最小实验可先读一个固定标志变量，再有界读取同一计数变量两次；是否执行、何时执行由统筹代理决定，不能改变正在轮询的状态读取程序来隐式扩大功能。

## 返回路径与尚待真机闭合的字段

厂商 `0x1b56b–0x1b58b` 先调用 `0x28a00` 汇总已接收帧的 `(总长-10)`，再经 `0x28a40` 把每帧 **偏移 10 起的正文**拼接。`0x1b58b` 从拼接长度减 **12**，`0x1b59a–0x1b5b4` 从拼接缓冲区 **+12** 复制剩余数据，并输出数据字节数。因此单项正文前 12 字节并非变量值本身，不能从帧偏移 10 直接解 INT/REAL。该函数没有在所选分支逐项校验 count/address/bits 回显。

独立 Simulator `mp_core.dll` 的 `_mp_mem_input`（VA `0x6590b91c`）在 selector3 正文操作 1 时进 `_mem_read`（`0x659082a8`）；它检查 `正文长度-6 == count*8`，保留 count，逐项回显 address/bits，并按 `(bits+7)>>3` 取数据，成功把正文 operation 改成 **2**。按这个实现，外层请求/响应布局为：

- 请求：op@8=1、count@10、address@14、bits@18，总长 22。
- 成功候选：op@8=2、count@10=1、address@14 与请求相同、bits@18 与请求相同、data@22；INT16 应恰为总长 **24**，REAL32 应恰为 **26**。
- 错误：该实现 op@8=`0x0f`、u32 error@10，总长 **14**；不能当数据。

**上述响应 operation=2 的直接证据来自 Simulator；未据此宣称真机兼容。** 厂商主机的前 12 字节处理与此形状相容，且 32bits→4B 调用点支持其单位，但仍需要真实 Easy521 的原始响应验证。未来只读解析器应核对完整长度/version/selector/sequence、count、address、bits、operation 和精确数据长度，拒绝尾随数据、错地址、错位宽、error/ack 冒充数据。保留所有原始字节；INT/REAL 的解释与大小端也应以厂商值转换/标志变量进一步交叉校准。

本轮未把这些地址解释成通用物理内存模型，未恢复数组、位变量、FB 实例、保持区或写变量 ABI，也未取得可声称探针实际变化的真机变量样本。下一验收的关键是独立保存固定变量的两次读数、时间、目标位置/产品、源码/构建候选哈希与完整响应，然后判断数值变化及固定标志是否支持当前应用执行；单独 RUN 或人工观察不替代这项证据。
