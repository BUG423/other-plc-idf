# 38. 写入 ACK 的精确 9 字节变体与历史判断纠正（2026-10-19）

首个真实原生下载在发送 GCM raw2 后收到完整 **9 字节 op6 ACK**，旧实现要求至少 10 字节并停止。本轮重新核对真实 H530 消费代码，确认写入成功回复的 DWORD 是可选字段；此前[报告26](26-native-application-protocol-2026-10-19.md)将 SetGCM 正常回复写为必须带 LE32 有误。修正记录写在本报告，原报告、首写失败及旧 `unknown` 原样保留。

本轮代理没有访问设备、重发命令或执行 Windows 程序；只静态读取组件、离线重解统筹采集的真机字节，并运行替身测试。[配套 JSON](38-empty-write-ack-2026-10-19.json)保存原始证据哈希、局部代码范围哈希、消费链、源码及测试来源。

## 真机事实与旧失败

原报告为 `.plcidf/native-download-plus1-20261019T080940Z/02-download-plus1.json`。请求 `130001014f9d00000200000000ff0100000002` 完整发送 19 字节，单次 IN 返回 `rc=0/count=9`、回复 `090001014f9d000006`。它包含总长 9、版本 1、selector 1、sequence `0x9d4f`、两字节保留字段和偏移 8 的 op6；**没有偏移 9 的 flag，也没有 DWORD 正文**。

旧程序保留 `request_outcome=unknown`，释放了 host 资源，未发送文件 open/write、raw3、RUN 或任何重放。这是当时解析器失败的事实。新解析器只离线解释已保存的 ACK，不能把原失败报告改成下载成功，也不能据此自动重发 raw2。

## 实际 H530 消费证据

来源仍是 SHA-256 `df8a449e9e374e2e0deb1aa8ca14403641cd2076129857536eee26f4e20b9698` 的 `H530Communicate.dll`，不是用 Simulator 枚举值推定设备协议。

`RVA288c0` 检查偏移 8 的 operation。op6 分支在 `288ea` 比较总长 `0x0a`，`288ee` 对总长小于等于 10 的回复直接跳 `288f5` 返回成功 1；只有长度大于 10 才在 `288f0` 读取偏移 10 的 DWORD。返回成功与是否存在 DWORD 分开。公共关联检查 `28610` 比较版本、selector 和 sequence，不读取偏移 9 的 flag。

三个当前支持的 writer 都沿真实消费链交叉核准：

| 动作 | 真实发送和 ACK 消费链 | 成功时是否使用 DWORD |
| --- | --- | --- |
| GCM Set | `de00 → c7d0 → c8c5 → 288c0` | 成功与可选值分别处理 |
| 下载 open2 | `d544…d55b` 发 selector4/op2，`d574 → 288c0`；内存路径 `d8c5` | 仅检查成功返回后继续 write5 |
| 下载 write5 | `d592/d594` 发 selector4/op5，`d5a1 → 288c0`；内存路径 `d8f4` | 成功直接跳结束分支；DWORD 仅在错误分支使用 |

这为两个文件 **writer** 的空 ACK 提供真实厂商调用链证据，未把 selector4 的所有回复一并放宽。读取打开仍必须得到实际文件长度；读取块仍必须有块号、数据长度和数据。

## 最小实现与边界

[codec](../../../tools/easy521_application_protocol.py) 仅对已验证请求动作 `gcm-set`、`file-open-write`、`file-write` 接受**精确 9 字节**、声明长度 9、版本 1、关联 selector/sequence、op6 的回复；原有精确 14 字节/u32 形式保持兼容。9 字节形式返回 `flags=null`、`ack_u32=null`、`ack_payload_present=false` 并注明 ACK 格式，不把缺失 DWORD 填成 0。保留字段仍作为原始值记录，不杜撰权限或状态含义。

原生 Session 只为这三个明确 writer 将帧长度下限改为 9，最终仍由 codec 校验。读取打开/读取块拒绝 9 字节 ACK；10、11、12、13 字节等未确认布局仍拒绝，不照搬厂商对部分长度可能越界读 DWORD 的宽松行为。op15 无论错误值是否为 0/1 都不是 ACK；缺少错误正文的 9 字节 op15 作为不完整错误帧拒绝。

每次发送次数、共享接收截止时间、资源释放和 `unknown` 失败边界保持。没有新增恢复会话、自动续传、重放、隐式 raw3 或 RUN。控制接口明确返回 ACK 是否有正文；ACK 不能当 CPU 状态读回或应用激活证明。

## 验证

离线重解原始 9 字节回复成功，结果保存在 `.plcidf/research-38-empty-write-ack-20261019/old-gcm-ack-reparse.json`，同时保留原报告的 `unknown`。新增测试覆盖原始向量、缺失字段、版本/selector/sequence/operation/长度错误、读打开和读块拒绝、其它短长度拒绝，以及真实厂商消费链允许的两个文件 writer。C ABI 替身验证 GCM/文件写空 ACK 可以按正常顺序推进，每个请求仍只发一次；读取空 ACK 不会伪造成零长度备份。

协议、应用 USB、原生 USB 合跑 **235 项通过，0 跳过**。这是离线解析和替身验证；本记录的物理观察只有统筹提供的那一次 GCM 9 字节回复，两个文件 writer 的 9 字节支持来源为厂商静态消费链，后续实际下装另保留证据。
