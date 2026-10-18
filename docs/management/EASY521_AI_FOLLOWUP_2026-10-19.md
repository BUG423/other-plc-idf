# Easy521 后续交付：调用链更正、已有变量与现场反馈入口

2026-10-19，续[首批交付](EASY521_AI_DELIVERY_2026-10-19.md)。本报告补充新结果并更正此前静态推导；历史报告、失败记录和原始真机返回保留。首批提交 `b9e2dcbc12deb89b81e3891c207ddf65034a8ba4` 的 [software/open-linux CI](https://github.com/BUG423/other-plc-idf/actions/runs/39153506180) 已全部通过，包括真实Linux ARM链接、MatIEC编译/执行、AI场景和仓库外安装。

## 1. 更正原查询的身份

独立核对RTTI、构造函数和真实执行槽后发现：12/15号报告曾把 `CRTUStateCommand` 错接到 `0x133d0`，实际该函数属于 `CReadEthercatCommand`。**已实测的 `0xff010000 / 4字节 = 0` 仍是真实响应，但不能称为CPU状态，也不能解释为STOP、RUN或耦合器模式。** 统筹承担集成判断并更正当前入口说明；详见[18号更正](../research/firmware/18-runtime-mode-correction-2026-10-19.md)。

通信代理逐项恢复三条链；统筹另外直接解析原始PE的三套RTTI/虚表，并核对真正状态读函数的代码哈希，见[独立复核](../../evidence/easy521-ai-loop-20261019/gcm-static-cross-check.json)。`GetPLCRunMode`实际是运行方式相关flags；真正CPU状态候选为 `GetGCMState → CGCMStateCommand → 0xdd30 → literal 0xff000000 / 1字节`。它由厂商自身代码证实，和Simulator使用相同编号不能替代这条证据。

厂商调用方分别把GetGCMState的枚举0/1显示为STOP/RUN，通信结果另有独立字段；2/3完整语义仍未知。固定索引查询的历史文件名保留兼容，新增显式 `--profile literal/gcm-state`，默认literal保持原请求。公开工具只发送选定的一次read，不开放任意索引或任何CPU模式/变量写入。

### 单次真机GCM读取已完成

独立审计通过后，统筹在设备租约内重新枚举BUSID `8-3 / 0471:a531 / Not shared`，执行：

```bash
.venv/bin/python tools/read_easy521_usb_runtime.py --profile gcm-state
.venv/bin/python tools/read_easy521_usb_runtime.py --profile gcm-state --execute --busid 8-3
```

第一条仍为纯预览。实际命令只发送一次18字节read，收到11字节数据帧：请求 `12000101f85c00000100000000ff01000000`，响应 `0b000101f85c0000050000`。版本/selector/随机序列/操作码/长度和cleanup均通过，返回原始 **u8=0**；见[真机记录](../../evidence/easy521-ai-loop-20261019/gcm-state-query.json)。按厂商静态映射，接口报告STOP；`device_calibrated=false`、`cpu_state_verified=false`与`application_execution_verified=false`仍保留，尚未用现场灯、扫描计数或程序身份独立标定。

未改变配置、清除端点、复位USB、请求CPU模式、写变量或下载应用；没有重发。仍由WSL调用Windows libusb0，AutoShop保持关闭。历史14字节literal响应没有被重新解释为CPU状态。

新的[仪器反馈](../../evidence/easy521-ai-loop-20261019/feedback-gcm-observation.json)引用这次真实返回与哈希，以前一轮为父轮，所有未知程序哈希保持null。两轮反馈经公开CLI校验并生成[关联摘要](../../evidence/easy521-ai-loop-20261019/feedback-ai-summary-gcm.md)，[集成记录](../../evidence/easy521-ai-loop-20261019/feedback-gcm-cli-integration.json)中两条命令均退出0。这是两轮通信研究记录，不是两轮ST应用部署/运行验收。

## 2. 从GVT独立恢复已有变量地址

构建代理新增[变量解析工具](../../tools/inspect_vendor_variables.py)与[17号报告](../research/firmware/17-existing-variable-bindings-2026-10-19.md)。从合法工程 `变量表.gvt` 的明确字段独立读取19项名称、类型和地址；19项与独立CSV地址/位宽及声明类型一致。解析后再打开参考ELF，8个待绑定全局变量的候选地址/类别全部吻合；不是从ELF复制值。

本工具只接受已知归档前导、ASCII简单标量、限定类型和严格记录/EOF；未知格式拒绝。它复用已有合法分配，不创建新变量，不修改ELF，始终 `allocator_implemented=false,elf_patched=false,downloadable=false`。统筹已用冻结GVT与CSV实际执行公开CLI，见[集成记录](../../evidence/easy521-ai-loop-20261019/variable-inspector-cli-integration.json)。

这使“固定现有变量/任务接口，修改ST逻辑”的绑定问题更具体。下一步仍须实现并核验受限符号注入，再完成配置/任务/文件包；完整应用构建与设备事务尚未完成。

## 3. 人工观察可直接交给AI

反馈代理新增[capture_plc_feedback.py](../../tools/capture_plc_feedback.py)和[操作说明](../FEEDBACK_CAPTURE.md)，复用原schema，提供 `create-human`、`validate`、`summary`：

- 保存迭代/父轮、需求版本、已知程序/设备字段、期望/实际、时间、来源与unknown。
- 显式校验真实日历、时区、本地附件路径与SHA256；默认只新建，拒绝覆盖旧记录。
- 多轮汇总拒绝重复ID、父轮环和同轮已知身份/哈希冲突，保留人工、仪器、AI推断与格式示例的原始标签。
- 结果只表示记录和附件校验，不提升设备或程序验收等级，也不执行AI、编译或PLC动作。

统筹用已有真实仪器记录执行validate和summary，2份附件哈希吻合；[集成记录](../../evidence/easy521-ai-loop-20261019/feedback-cli-integration.json)与[原索引反馈摘要](../../evidence/easy521-ai-loop-20261019/feedback-ai-summary-raw-index.md)已保存。摘要里的历史raw0仍按第1节更正解释；没有伪造用户现场观察。使用示例：

```bash
# 读取真实仪器记录，不改成human、不覆盖原证据。
.venv/bin/python tools/capture_plc_feedback.py validate \
  evidence/easy521-ai-loop-20261019/feedback-runtime-observation.json --evidence-root .

# 将已记录的观察汇总给当前终端AI；不会自动修改或下装。
.venv/bin/python tools/capture_plc_feedback.py summary \
  evidence/easy521-ai-loop-20261019/feedback-runtime-observation.json --evidence-root .
```

`jsonschema>=4.18,<5`已加入仓库CI依赖，主运行包依赖保持原范围。工具仍为源码仓库工具，尚未成为已安装的plcidf子命令。

## 4. 复核与当前缺口

独立审计代理复跑反馈38项与GVT10项软件测试通过，未发现P0/P1；仅操作本地临时文件，没有设备动作。工具真实文件实验、软件测试与CPU候选读取分别验收，不能叠加成两轮程序运行。

仍开放的工作为：固定已有接口的符号注入与完整包装、严格Linux的ST编译及USB后端、独立下装/程序身份，以及同一程序的实际扫描、停止与规则变化。设备精确SoC、BSP、I/O和系统恢复路径仍未证明；不需要先替换系统固件才能推进应用层协议。

本轮工具完整回归 **351项通过**，包含真实LLD合成ARM链接正负例；另904个子测试不重复叠加。源码/monitor未修改，复用其765项通过，合计 **1116项不重叠软件测试**，51项跳过及1项真实Windows枚举排除范围不变。新codec94项、USB worker67项、反馈38项、GVT10项都是工具351项的子集。真实GCM、GVT公开命令及反馈汇总均另有独立记录；[后续验证清单](../../evidence/easy521-ai-loop-20261019/followup-validation.json)记录当前代码哈希，首批清单保持原哈希不覆盖。
