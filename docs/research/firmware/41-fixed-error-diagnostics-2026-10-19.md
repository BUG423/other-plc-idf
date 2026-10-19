# 41. 固定系统故障读取与运行等待条件（2026-10-19）

新增三个固定只读诊断 profile：当前错误数量、完整错误列表和错误实例列表。它们通过既有 selector3/op1 读取协议访问已核准的系统符号，不提供任意地址、偏移、数量或写操作参数。实现、实际来源和离线验证见[配套 JSON](41-fixed-error-diagnostics-2026-10-19.json)。本轮没有操作真机或 Windows；不能将测试中的错误码当成设备已发生故障。

## 来源和型号边界

真实 InterfaceMgr 导出 `GetCurErrLst`、`GetCurErrInst`，H530 对应 RTTI 为 `CGetCurErrLstCommand`、`CGetCurErrInstCommand`。执行链 `e0b0→e110`、`dec0→df20` 读取 `_CurErrLst`、`_CurErrInst`，发送 selector3/op1；名称由当前项目解析为地址。初始化输出缓冲区的清零发生在主机内存，不是清除 PLC 故障的请求。

冻结工程 `VarNameAndAddr.csv` 第 76/75 行分别给出 `0x06200700 / 4048 bits` 和 `0x06201900 / 1344 bits`。本机 `SysVarDef.xml` 的系统基址 `0x06200000` 及 `0700/1900` 定义与之相符，`SysDataTypeDef.xml` 定义错误列表首成员为 `Quantity : INT`，随后 42 条 `INT/INT/DINT/DINT` 记录，合计 `2+42×12=506` 字节，恰为 CSV 的 4048 bits。错误实例为 42 个 DINT，合计 168 字节。这里没有对不透明地址作偏移运算，字段偏移只用于解析已经收到的字节。

GVT 只包含本工程 19 个业务全局，不能声称上述系统符号经 GVT 独立验证。另一个差异也必须保留：当前 H530 通用错误列表调用硬编码 962 字节，而此工程 CSV/XML 定义 506 字节；本实现使用已选择工程的明确宽度，不直接搬用通用客户端的大长度，不外推到其他型号/版本。

## 固定接口

| profile | 符号及类型 | 固定地址 | 数据/完整回复字节 |
| --- | --- | --- | --- |
| `cur-error-count` | `_CurErrLst.Quantity`，INT16 | `0x06200700` | 2 / 24 |
| `cur-error-list` | `_CurErrLst`，完整结构 | `0x06200700` | 506 / 528 |
| `cur-error-instances` | `_CurErrInst`，42×DINT | `0x06201900` | 168 / 190 |

建议先读取最小数量，再根据情况单独选择完整结构。默认预览不加载 libusb、不枚举、不获取租约、不使用随机事务号，也不自动轮询：

```bash
.venv/bin/python tools/read_easy521_usb_memory.py \
  --transport linux-native --profile cur-error-count --linux-device 001:002

.venv/bin/python tools/read_easy521_usb_memory.py \
  --transport linux-native --profile cur-error-list --linux-device 001:002
```

显式加 `--execute` 才发送一次固定读取。Linux BUS:ADDRESS 必须按当次设备位置提供；仍使用既有设备身份、配置 2、CDC 数据接口 1 与端点检查，不自动解绑驱动。原 Windows helper 的固定表和边界同步，Windows 路径仅做离线替身测试。

错误列表返回数量、容量、前 `Quantity` 条活动记录和全部 42 条原始记录，字段为 ErrorCode、ComponentID、Location、Timestamp。16 位字段同时保留有符号值与原始无符号位值；Location/Timestamp 不猜测单位。数量不在 0–42 范围时拒绝，保留传输原始证据，不截断或编造错误。实例数组的 DINT 值保持不透明，不跟随它们读取其他地址。结构的 `raw_bits_u32` 为 null，完整原始字节仍保留，避免把整个结构误叫成一个 u32。

本机 `ErrorMsg.xml` 提供组件及错误码字典，包括程序结构、版本、ELF 加载、段加载、重定位等分类。只有设备实际返回对应组件/错误码后才能将它与字典关联；字典候选不构成这次 RUN 未成功的根因。`GetErrorInfo(long)` 只是将已有错误值转为本地文字的接口，不是远端故障查询。

## 精确边界和跨 USB 包

原生 transport 根据固定 profile 的已知宽度计算 `22+ceil(bits/8)`，三个新增 profile 的最大回复分别严格为 24/528/190 字节。旧 profile 使用各自的精确上限，不因新增结构放宽。op15 错误帧仍须恰为 14 字节并作为失败处理；正常数据必须关联版本/selector/sequence、回显 count/address/bits，并精确匹配该结构长度。

完整 528 字节回复用 512+16 两次 IN 收齐；最多 4 次 IN 的预算、同一接收截止时间、单次 OUT 和释放资源规则保持。缺后包、超时、空包或尾随内容立即失败，发送之后保留 `unknown`，不自动重连、重放或改长度重读。诊断入口没有错误清除、重置或 CPU 控制操作。

## RUN 的厂商等待条件

AutoShop 上层 VA `15f369f` 将目标置 1，`15f36b0` 发一次 SetGCM；通信成功后，`15f37b2` 限定最多 500 次 GetGCM，`15f38b6` 只有读回值为 1 才认定运行成功，否则 `15f38d8` 调用 `Sleep(10ms)` 后继续只读。IAT `1eb1868` 已核对为 Sleep。其等待约为 5 秒加通信耗时，是该主机版本的策略，不是固件承诺的完成时限；不会在循环中重复 RUN。

另有 STOP 上层路径 `44e4ca…44e520`，一次 STOP 后最多 10 次、每次先 Sleep 100ms，再读取 GCM，直到值 0。底层 SetGCM ACK 仅是通信成功，因此立即一次读回为 0 尚不足以推断永久 RUN 失败。上述等待规则供统筹编排使用；本次单次只读接口不暗加循环，也不触发新的 RUN。长期读回及现场 ERR 灯应作为独立真机证据保留。

## 验证

相关 memory codec、reader、native transport 与应用协议合跑 **411 项通过，0 跳过**。新增验证覆盖精确宽度、数量容量、完整结构解析、实例不解引用、528 字节跨 512+16 包且仅一次 OUT、缺失/尾随后包拒绝、原 pcmd 不能接受大回复、Windows helper 原始证据校验及 PS 固定表一致性。所有通信均用替身，未执行 Windows helper 或访问 USB。

三个实际 Linux CLI 预览均成功，原始报告位于 `.plcidf/research-41-fixed-diagnostics-20261019/`，全部 `device_access=false`。实际设备故障内容、恢复后的诊断读取和修复后的运行效果由统筹另行记录。
