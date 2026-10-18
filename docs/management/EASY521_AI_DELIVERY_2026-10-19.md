# Easy521 AI 闭环：独立 USB 查询、Linux 链接与仓库统一

2026-10-19。本轮按用户要求保留单一 `main` 分支，继续推进“Linux 终端描述需求 → AI 修改有规范注释的 ST → 编译下装 → 设备数据与人工观察 → 再修改”。三名 AI 技术代理分别负责通信、构建事务和闭环架构，另一代理独立审计，统筹负责主机/真机操作与 GitHub；没有聘请外部人员。

**本轮已在真机上绕开 AutoShop 查询到原始运行态接口数据，并在 Linux 实际执行厂商顺序的两级对象链接。完整 ST 应用下装和 CPU 执行仍未验收。** Windows 驱动桥接、Linux 对象链接与最终严格 Linux 原生目标分别记录。

## 用户需求与实施分工

[需求基线](../requirements/EASY521_AI_LOOP_2026-10-19.md)、[ADR-0005](../architecture/ADR-0005-easy521-ai-control-loop.md)和[任务表](EASY521_AI_TASKS_2026-10-19.md)给出依赖、输入输出、责任代理和验收条件。核心区别是：修改现有参数需要数据接口；修改控制算法需要新的应用构建与部署；CPU 模式与系统固件分别处理。

| 本批执行者 | 实际交付 | 后续缺口 |
| --- | --- | --- |
| `firmware_sources` | 恢复 USB 帧与响应合同、离线 codec、固定只读查询 Python 入口 | 状态枚举、CPU 模式命令、设备永久身份与 Linux USB 后端 |
| `deployment_contracts` | 真实 Linux 两级链接工具、变量符号注入代码规则、有序文件事务及配置封装入口 | 经过校验的变量分配/名称映射、完整包生成、事务线上验证 |
| `portable_architecture` | 闭环需求、架构、10个工作包、人工/仪器反馈 schema 和 unknown 示例 | 消费真实记录的反馈采集/汇总入口与两轮硬件闭环 |
| `architecture_review` | 离线独立审计、协议/链接/查询负向测试与 CI 覆盖检查 | 真机运行与严格 Linux 原生结果仍需独立验收 |
| 统筹 | 单分支、README、协作/注释规范、真实 USB 实验、公开命令集成、反馈样例和 GitHub | 汇集后续证据，继续推进具体缺口 |

注释标准已写入根 [AGENTS.md](../../AGENTS.md)：说明接口、单位/范围、周期与状态边界，解释设计原因；协议偏移、枚举和特殊 ABI 字段引用证据；修改规则时同步注释和场景。它是项目工程约定，不宣称功能安全认证。

## 真机：直接从 USB 驱动获得返回

01:46（上海）主机只读枚举确认：USB 设备为 `8-3 / 0471:a531 / H5U USB Device / Not shared`，驱动服务为 `libusb0`；Realtek 物理网口仍为 Disconnected/0bps。Windows 当前进程不是管理员，未安装/更换驱动或执行 USB bind/attach。见[主机状态](../../evidence/easy521-ai-loop-20261019/host-state.json)与[USB枚举](../../evidence/easy521-ai-loop-20261019/usb-enumeration.json)。

01:51 通过已检查哈希的 x64 libusb0 直接读取标准描述符，得到：

- 厂商字符串：`Linux 3.10.65 with sunxi_usb_udc`。
- 产品字符串：`Easy52x USB Device v2.4`。
- GET_CONFIGURATION 当前返回 **2**；DLL/驱动版本均为1.2.6.0。

这是当前设备的 USB 自述，仍不能独立认定准确 SoC、内核完整性、板级 I/O 或 PLC 应用固件版本；USB产品字符串版本也不等于此前查询的PLC固件6.11.0.1。原始记录见[描述符实验](../../evidence/easy521-ai-loop-20261019/usb-standard-descriptors.json)，可复用只读 helper 为 [read_easy521_usb_descriptors.ps1](../../plc/tools/read_easy521_usb_descriptors.ps1)。结构和 API 对照了 [libusb-win32 官方头文件](https://raw.githubusercontent.com/mcuee/libusb-win32/master/libusb/src/lusb0_usb.h)。

独立 bulk 查询前，确认现存 AutoShop 只打开我们建立的实验工程 `PLCIDF_LAB_20261017\BESS_E521_DEV`，Scintilla 没有未保存修改且不处于在线只读状态，主窗可用；随后正常关闭该进程并确认退出。没有保存按钮、强杀或 PLC 模式动作。详见[独占准备记录](../../evidence/easy521-ai-loop-20261019/exclusive-session-preparation.json)。**AutoShop 已退出，本轮没有自动重新打开。**

01:57 原型持设备租约，在已激活的配置2下 claim 接口0，通过OUT `0x02` 单次发送18字节读取请求，由IN `0x81`收到14字节返回。请求版本、selector、sequence、数据操作码和长度均通过 codec 校验，原始 u32 为 **0**；claim/release/close 均返回0。没有 set_configuration、clear_halt、reset、CPU RUN/STOP、业务变量写入或应用下载。见[原型实测](../../evidence/easy521-ai-loop-20261019/single-usb-runtime-query.json)。

### 可重复使用的终端入口

```bash
# 默认预览：不执行 Windows、USB 或设备租约操作。
.venv/bin/python tools/read_easy521_usb_runtime.py

# 实际读取：先关闭 AutoShop，并核对当次 usbipd list 中的位置。
.venv/bin/python tools/read_easy521_usb_runtime.py --execute --busid 8-3
```

[Python worker](../../tools/read_easy521_usb_runtime.py)在设备锁内重新枚举，要求唯一匹配设备与显式BUSID一致且为Not shared；它不切换所有权，也不关闭AutoShop。已打开AutoShop时，固定[PowerShell helper](../../plc/tools/read_easy521_usb_runtime.ps1)拒绝执行。每次执行使用随机u16序号、发送一次固定读取请求并有界收帧，复核helper哈希，严格解析响应及清理结果；不重发。随机序号降低迟到响应碰撞概率，不提供认证或绝对鲜度保证。

公开命令首次集成在 PowerShell 类型求值处失败：`[ushort]`不是该宿主接受的别名，USB读取函数没有执行。失败[原文](../../evidence/easy521-ai-loop-20261019/public-runtime-worker.json)保留。改为 `[uint16]` 后以新的随机序号再次显式集成，返回 `status=observed,raw_value=0,state_semantics=unknown`，完整记录见[最终公开命令实测](../../evidence/easy521-ai-loop-20261019/public-runtime-worker-final.json)。这是修复后的新试验，worker没有自动重试。

**该命令仍由 WSL 调用 Windows 驱动，不是严格 Linux 原生 USB 后端。** 它证明独立请求/返回可以绕开 AutoShop，不证明0就是STOP或RUN、CPU已扫描、新程序已执行或端子动作。真实型号/程序字节身份和状态枚举仍待校准；两个只读请求不能充作两轮程序部署验收。

## Linux：实际完成两级对象链接

[研究16](../research/firmware/16-application-transaction-2026-10-19.md)恢复并执行了：

1. `MAIN.o + SBR_001.o + _SysLib.o → stuserprog.o`。
2. `stuserprog.o + _STCLib.o → stprog.o`。

使用仅下载并解包的Ubuntu LLD20，在冻结副本上执行，不运行厂商EXE/DLL。新分支结果的代码节、ARM属性及三个重定位节与同轮厂商参考逐字节相同；两个ELF完整文件仍不同。

规范化符号表后，差异精确剩下8个工程全局变量：厂商注入自定义类型8及项目地址。进一步定位到 `SInfo` 12字节记录、地址查询来源和 `STRelocation` 的 `st_info/st_value` 写入规则；完整变量分配器、名称/类型映射尚未复现。工具没有抄参考地址或实施patch。下一步是从合法项目变量记录产生可校验的绑定，再推进配置封装和文件事务。

[link_vendor_application.py](../../tools/link_vendor_application.py)已实现明确输入顺序、冻结副本、原文件与工具哈希复核、每阶段超时和无重试。成功只表示 `scope=two-stage-relocatable-object-link`，始终 `downloadable=false`。真实厂商输入重复输出一致，合成ARM正例和重复符号负例也已执行。

下载端已追到有序文件表、事务状态 raw2/3、64字节配置头及各payload生成入口；厂商日志标签不总是远端文件名。这些是静态合同，尚未通过独立宿主执行应用事务。完整文件、状态意义与字节绑定见研究16，不发送未经验证的事务。

## 人工反馈与程序迭代

本轮增加[反馈schema](../../examples/easy521_feedback/feedback.schema.json)和[unknown示例](../../examples/easy521_feedback/unknown-feedback.json)，记录迭代/父轮、需求版本、源码/构建/候选/设备身份、动作、期望/实际、时间和观察来源。反馈不承载连接配置或动作授权。

已用真实USB观测形成[仪器反馈记录](../../evidence/easy521-ai-loop-20261019/feedback-runtime-observation.json)：保存原始值0和证据哈希，程序哈希保持null，CPU运行结论与需求结果保持unknown；没有伪造用户现场观察。当前反馈采集CLI与自动汇总尚待实现。

系统jsonschema缺少内置date-time检查器，本轮显式注册标准库日历校验后验证2个正例、6个负例，并独立核对附件哈希，见[反馈验证](../../evidence/easy521-ai-loop-20261019/feedback-validation.json)。schema合法不证明内容真实，也不提升设备身份或程序执行等级。

## 仓库与验证

原PR #13已正常合并为 `f2fc43215b1ba9419b7dae02d371aac10c2c0df1`，工作分支在确认包含于main后删除，本地与GitHub只保留main。16个Issue/PR正文中指向已删除分支的文档URL已改为main，检查表与状态保留；[修复记录](../../evidence/easy521-ai-loop-20261019/github-branch-link-repair.json)。

README对照了用户的 [esp32-lab](https://github.com/BUG423/esp32-lab)、[esp32-web-tools](https://github.com/BUG423/esp32-web-tools)、[app-remote-terminal](https://github.com/BUG423/app-remote-terminal)和[other-blocks](https://github.com/BUG423/other-blocks)：标题/徽章/简介、架构、核心特性、快速上手、测试与末尾协议声明统一，详细日志放到docs；移除过期的固定测试计数徽章。许可目前保留原PolyForm，README与LICENSE/包元数据一致；其他仓库的CC BY-NC-ND许可是否一并采用已单独征询，未将未回复当作变更授权。

本地源码/monitor **765项通过、51项跳过、1项真实Windows枚举排除**；工具 **284项通过**，包含新codec85项、链接14项、USB worker57项，这些子集不再叠加，总计 **1049项不重叠测试通过**。工具初次合并测试因统筹启动参数解析了 `ld.lld` 符号链接、误用通用`lld`入口而失败；保留该记录并修正启动参数后通过，worker代码未因此更改。详见[本轮验证](../../evidence/easy521-ai-loop-20261019/software-validation.json)。

CI现统一收集tools下的pytest函数与unittest类，避免漏跑codec；Linux任务安装LLD执行真实合成ARM链接正负例。main的新提交由远程软件、真实ST工作流和独立安装检查验收。

定期进展工作流已进入默认main并启用，计划每周一09:00（上海）向总跟踪Issue #12追加任务摘要，GitHub调度可能延迟。它不进行后台研发、访问PLC或代替运行验收。后续优先工作是全局变量地址/类型生成、CPU原始状态独立标定、完整应用事务及反馈采集；T07/T08/T09/R01/R02保持开放。
