# 运行模式与 CPU 状态：旧调用链更正（2026-10-19）

**12/15 号报告把 `CRTUStateCommand` 错接到了 `0x133d0`。本轮沿两套虚表的真实执行槽交叉核对，证实该函数属于 `CReadEthercatCommand`。此前真实读到的 `0xff010000 / 4B = 0` 仍是有效的原始响应事实，但不能解释为 CPU STOP，也不能套用 GetPLCRunMode 的耦合器模式位。** 历史记录按原貌保留，本报告修正其推导，结构化证据见[JSON](18-runtime-mode-correction-2026-10-19.json)。

本轮只读取本机合法安装的 AutoShop/InterfaceMgr/H530Communicate 二进制、RTTI、资源字符串和调用点，没有执行 Windows、GUI、DLL、USB、网络或设备操作。没有修改已完成的真实观测、PS helper 或请求行为。以下地址分别标明模块；AutoShop 首选基址为 `0x00400000`，两 DLL 为 `0x10000000`。代码区间 SHA256、字符串资源位置和完整文件 SHA256 均记录在 JSON；它们是固定版本的静态证据，不是动态调用地址。

## 三条路径必须分开

| 入口/对象 | 构造与真实虚表执行槽 | 共同读函数输入 | 输出/含义边界 |
| --- | --- | --- | --- |
| `GetPLCRunMode` | InterfaceMgr facade RVA `0x69a80` → 实现槽 `+0x1b8` → H530 RVA `0x7310`；构造 `CRTUStateCommand`，虚表 `0x7bb24`；**执行槽 `+0x3c = 0x1df70`** → `0x1dfb0` | `0xffc00000`；末参 `0`；进入 `0xc5c0` 按 protocol 元数据取得长度，而非 literal `0xff010000/4` | `0x1dfea–0x1dff4` 将返回缓冲区首 DWORD 原样回填到对象 `+0x2c` 指向的调用方地址；调用方使用低两位/bit4 处理运行模式相关限制 |
| 已执行的 literal `0xff010000/4B` | **`CReadEthercatCommand`**，虚表 `0x7affc`；执行槽 **`+0x3c = 0x13390`** → `0x133d0` | literal `0xff010000`、长度 `4`、末参 `1` | `0x13406–0x1340c` 回填首 DWORD；RTTI 指向 EtherCAT 读取。原始值 0 的具体设备语义仍未知 |
| `GetGCMState` | InterfaceMgr facade RVA `0x689e0` → 实现槽 `+0xa4` → H530 RVA `0x5050`；构造 `CGCMStateCommand`，虚表 `0x79e54`；执行槽 **`+0x3c = 0xdcb0`**，读分支 `0xdd30` | **literal `0xff000000`、长度 `1`、末参 `1`** | wire 首字节 0/1/2/3 分别映射同值 EnumGCMState；其他值令内部枚举 `-1`、通信结果 `2`。调用方静态 UI 支持 0=停止、1=运行；2/3完整命名未恢复 |

`CRTUStateCommand` 和 `CReadEthercatCommand` 共用析构槽 `+4 = 0x13330`，但**析构函数不是命令执行函数**。本轮逐项核对 RTTI 和 `+0x3c`，避免根据附近代码地址或共享析构函数继续误接。`GetPLCRunMode` 的 `0xc5c0` 成功分支仍把输入 index 原样放到输出首 DWORD，第二 DWORD 来自元数据；没有把 `0xffc00000` 转为 `0xff010000` 的证据。

`CReadEthercatCommand` 的构造来源也已核对：H530 实现 RVA `0x4ac0` 在 `0x4ae8` 写入虚表 `0x7affc`，设置调用者输出指针于对象 `+0x2c`。它与 `GetPLCRunMode` 的 `0x7310 → 0x7bb24` 是两套不同构造路径，不能只因输出均为 DWORD 就混同。

## GetPLCRunMode 的多个独立使用点

AutoShop 通过 IAT VA `0x01eb1408` 调用此入口。六个直接调用点均已定位。以下四处有独立业务上下文的交叉印证：

| AutoShop 调用点 VA | 先检查通信结果 | 再检查设备数据 | 非零分支的字符串资源 |
| --- | --- | --- | --- |
| `0x015f1aeb` | EAX 保存至 `[ebp-0x30]`，必须等于 `1` | `[ebp-0x2c] & 3` | ID `0x1cac`：提示处于耦合器模式，不能切换运行 |
| `0x015f6249` | EAX 保存至 `[ebp-0x1c]`，必须等于 `1` | `[ebp-0x270] & 3` | ID `0x1cb1`：提示耦合器模式下用户程序下载后不运行 |
| `0x00781e5a` | EAX 保存至 `[ebp-0x200]`，必须等于 `1` | `[ebp-0x1fc] & 3` | ID `0x1cb8`：提示耦合器模式下禁止写防火墙配置 |
| `0x0078280e` | EAX 保存至 `[ebp-0x368]`，必须等于 `1` | `[ebp-0x364] & 3` | ID `0x1cb9`：提示耦合器模式下禁止读防火墙配置 |

另一调用点 `0x016b74ba` 在通信成功后检查数据 `&0x10`，非零分支加载资源 `0x1c8d`，提示实际组态有耦合器扩展卡、运行模式由扩展卡拨码确定。该处还有一个先算 `&1` 但随后被 `&0x10` 结果覆盖的局部变量，不能把这个被覆盖值当作额外分支证据。第六处 `0x015da57d` 将数据 `&3` 缓存到对象 `+0x34`，通信失败也会把该缓存置零；因此**单独看缓存零值不能证明通信成功或设备模式**。

这些证据支持 `GetPLCRunMode` 是运行方式/耦合器相关 flags，不能依据名字把它当 CPU 扫描态枚举。即便将来真实读到这个入口的 DWORD=0，也只能说它没有触发本版本代码检查的相应标志，不能自动推出 RUN 或 STOP。当前已取得的 `0xff010000` DWORD=0 更不属于此入口。

## 真正 CPU 状态只读候选及状态映射

`GetGCMState` 的公开签名是输出 `EnumGCMState*`，随后是 `CWnd*`、`EnumExecuteMode`、`EnumCommandLevel`，返回 long 是通信结果。H530 `0x5050` 构造 60 字节对象；`+0x30=0` 选择读，输出地址存在 `+0x2c`，内部状态 `+0x38` 初值 `-1`。`0xdcb0` 调用 `0xdd30` 后，`0xdd0b–0xdd11` 把内部枚举写入调用方输出地址，另外把通信结果写入独立结果全局。不得把二者的数值 1 混为一个字段。

读分支 `0xdd30` 对 `0xc6c0` 传入长度 1、编号 `0xff000000`、末参 1。因此本候选来自**厂商自身的构造/执行路径**，不依赖 Simulator 的同名接口。与 Simulator 的 index/长度相同只是交叉线索，不能证明全部协议、会话、版本或设备兼容。

AutoShop 的状态映射提供两处直接 UI 证据：

- `0x015f10dd` 调 GetGCMState，`0x015f10e9` 先确认通信结果等于 1；`0x015f11c3` 比较输出枚举为 0，随后加载资源 ID `0xf8fc`：`PLC处于停止状态`。
- `0x015f18e4` 调 GetGCMState，`0x015f18f0` 先确认通信结果等于 1；`0x015f19ca` 比较输出枚举为 1，随后加载资源 ID `0xf8a8`：`PLC处于运行状态`。

此外运行流程在 `0x015f1c33` 重新调用 GetGCMState，分别检查通信结果 1 和输出枚举 1；否则走“未能正确切换至运行状态”资源分支。这是状态与通信结果相互独立的额外印证。枚举 2/3 的完整命名、物理拨码优先级、扫描/任务计数与外设运行证明仍未知，未实现或调用任何 SetGCMState 路径。

下一步最小设备验收候选为 selector 1/read 1/index `0xff000000`/数据长度 1，其请求仍为 18 字节，正常数据响应应为 11 字节。必须新增明确的厂商 GCM profile 后单独验收，不能静默改变已有 `0xff010000/4B` helper 的行为，不能把历史 14 字节响应重新解释成 CPU 状态。这一验收尚未由本代理执行。

## 当前工具的独立更正

纯离线 codec 新增中性名称 `VENDOR_LITERAL_FF010000_4`；旧 `VENDOR_RUNTIME` 和字符串 `vendor_h530_runtime` 仅保留兼容，注释明确它不是 CPU 状态。新增 `VENDOR_GCM_STATE` 的输入来自本报告的厂商执行路径，正常响应严格为 1 字节且值只允许 0–3；错误帧仍要求 4 字节错误值，绝不当作状态。Simulator profile 继续独立存在。

Python worker 新增固定选择 `--profile literal|gcm-state`，默认 literal 保留既有请求；执行向 helper 传入匹配的 `-ReadProfile`，并严格校验其顶层返回标识。只有 gcm-state 可以附加基于本版本 UI 的静态 STOP/RUN 解释，`device_calibrated=false`、`cpu_state_verified=false`；这不替代真机独立标定。所有原始返回、随机序列关联与 helper 前后 SHA 保护继续保留。

验证：94 项 codec 测试与 67 项 worker 测试共 161 项通过，均为自主合成字节/fake runner/临时本地锁。未执行新 profile 的设备请求。代码与这些测试仅证明静态子集和软件失败处理符合约定。
