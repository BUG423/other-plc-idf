# 进展记录

## 2026-10-19接入24 V：Y0真机运行、停止与独立USB读回

[20号实测报告](../research/firmware/20-powered-led-bench-2026-10-19.md)记录本轮结果，以下旧快照保留其当时结论。

- 用户明确24 V已接、全部输出悬空无负载；新鲜USB定位为7-2/0471:a531。先上载旧探针并备份，再在独立工作副本编写有详细注释的Y0灯程序。
- 原探针revision52102、计数9602→735经新ReadMemoryEx worker真实读回；固定请求/响应、地址类型及非原子采样边界保留。
- 灯程序完整厂商编译0错误、USB下装成功、厂商程序比较通过；用户明确观察输出区Y0闪烁。随后F6 STOP日志成功、用户确认熄灭，独立GCM=0/M8000=false/M8001=true；F5恢复RUN日志成功，三项反转为1/true/false。
- 30秒只读轮询在桌面操作期间显式暂停，恢复后有界继续；不是持续后台研发。用户观察、CPU状态、传输日志、工程比较和设备字节身份分别记录。
- 原始路径冻结错误导致一次真实F8预编译失败，修复属性并新显式编译后才下载；失败与修正记录均保留。下载后两个ARM对象漂移，未冒充设备字节一致。
- 新固定读工具与147项离线正负例、sdist/wheel资源和隔离preview已交付；完整工具回归及分发验收另有记录。源码/monitor与远程CI结果按实际补存。

当前仍经Windows编译器/驱动和监督桌面操作；本次1920布局未接入现有严格flash适配器，具体下一批见[适配计划](../architecture/EASY521_CURRENT_DESKTOP_ADAPTER_PLAN_2026-10-19.md)。精确周期、负载电气性能、错误恢复、完整Linux原生编译/包装/下装及设备字节身份仍未验收。保留灯应用RUN，原BESS恢复未演练，T08/T09/R01保持开放。

## 2026-10-19后续：调用链更正、真正GCM查询与反馈CLI

[最新后续报告](EASY521_AI_FOLLOWUP_2026-10-19.md)补充并更正下列首批快照，历史记录保留。

- **静态更正**：12/15号将CRTU误接到EtherCAT执行函数。18号以三套RTTI/构造/执行槽更正；此前ff010000/4B的raw0是真实原始响应，不能解释为CPU状态或GetPLCRunMode flags。
- **真正GCM读取**：通信代理和两次独立复核确认厂商GetGCMState读取ff000000/1B。统筹以新profile单次实测，18字节请求得到11字节帧、u8=0；厂商静态映射为STOP，现场灯/扫描/程序身份仍未校准。无模式、变量或应用写入；仍用Windows驱动。
- **变量解析**：独立读取GVT19项已有合法分配，与地址CSV/声明类型一致，解析后对照的8项候选符号绑定吻合；未实现新分配或ELF注入。
- **反馈工具**：create-human/validate/summary已交付，38项正负例和独立审计通过。实际仪器记录/附件及父轮摘要已由公开CLI集成；没有伪造人工观察。T14对应Issue #18。
- **验证**：首批b9e2dcb远端software/open-linux全通过。当前工具351项通过（含94codec/67USB worker/38feedback/10GVT，均为子集），另904个子测试不重复叠加；与未修改源码/monitor的765项合计1116项不重叠软件测试。新提交由CI复验。

供电/灯/负载的当次人工信息仍待回覆；网口此前仍断开。完整应用构建/独立下装/程序身份/实际扫描和严格Linux原生仍开放，计划与Issue不因通信或软件测试通过而结案。

## 2026-10-19：单分支、独立真机 USB 查询与 Linux 两级链接

用户明确最终闭环为Linux里AI编写有规范注释的ST、编译下装、接收设备/人工观察、继续修改，并要求仓库只保留一个分支及统一README。需求、架构、任务和真实交付见[本轮报告](EASY521_AI_DELIVERY_2026-10-19.md)、[需求基线](../requirements/EASY521_AI_LOOP_2026-10-19.md)、[ADR-0005](../architecture/ADR-0005-easy521-ai-control-loop.md)与[分工表](EASY521_AI_TASKS_2026-10-19.md)。

- **仓库**：PR #13已正常合并，全部提交保留；删除已合入的工作分支后本地/远端只有main。README参考用户4个其他仓库重整，移除旧测试计数；根AGENTS固化单分支、分工与详细注释规范。16个Issue/PR的旧分支URL修到main。许可暂保留现有PolyForm，与README/包元数据一致；是否采用其他仓库的CC许可已单独询问。
- **真机通信**：当前USB描述符直接读到Linux3.10.65/sunxi、Easy52x v2.4，当前配置2。先核实无未保存/在线修改，再正常退出精确实验AutoShop，独占USB。原型和公开worker各单次已知只读请求均收回匹配数据，原始u32=0；状态枚举、CPU身份与执行仍unknown。公开worker默认preview、执行时fresh枚举/lease、随机序号、校验helper哈希、完整收帧、不重试。保留首次PS类型别名失败与修复后的成功记录。没有setconfig/reset/clearhalt、模式/变量/应用写入；AutoShop保持退出。
- **Linux链接**：恢复并实际执行POU+_SysLib→stuserprog、再+_STCLib→stprog；代码和重定位节与当前参考逐字节相同。差异精确剩8个工程变量的类型/地址后处理，已追到SInfo来源与STRelocation写入，未复制地址或patch。新工具始终downloadable=false；完整配置包装、文件事务与设备字节绑定仍开放。
- **闭环与反馈**：10个任务有责任/依赖/验收与研究退出条件；新增反馈schema/unknown例子，并保存本轮仪器raw0的真实反馈，program hash全部null、结论unknown。未编造用户观察，反馈CLI仍待实现。严格Linux原生与WSL调用Windows驱动/编译器分别标记。
- **验证**：源码/monitor765 passed、51 skipped、1个真实枚举排除；工具284 passed（新codec85、链接14、USB worker57为其子集），共1049项不重叠通过。真实Linux LLD合成正负例、原厂对象链接、公开Windows USB worker、反馈格式/附件哈希另有证据。修复测试启动参数把ld.lld误解析为lld的问题，初次失败保留。CI统一pytesttools，并加入真实Linux LLD正负验证。见[本轮验证](../../evidence/easy521-ai-loop-20261019/software-validation.json)。

网口01:46仍Disconnected/0bps，未做猜测IP扫描；今日24V/开关/负载和RUN灯未重新观察。设备USB自述不能当作准确SoC/BSP证据。完整Easy521新应用执行闭环和严格Linux原生尚未通过。定期进展工作流现已进入默认main并启用（每周一上海09:00，可能延迟），仅报告任务元数据，不是无人值守研发或PLC控制。

## 2026-10-18：深究 Easy521 的最短直接控制入口

三个 AI 技术代理并行核对手册、通信库和下载链，另一 AI 代理独立审计新工具；统筹检查现场主机连接并复核交付。按此前 Easy521 实物研究，用户新写的 Easy512 尚未确认为另一型号。详细结果见[直接控制报告](EASY521_DIRECT_CONTROL_2026-10-18.md)。

- **Linux 数据入口**：新增 `tools/easy521_d_control.py`，显式 IP、D0–D7999、原始 uint16、FC03读取；单字写默认离线预览，`--execute` 后一次FC06并在同连接读回。零重试、禁止自动重连；写后异常/超时/不一致为unknown。独立工具不改变原只读monitor，也不宣称CPU运行、业务生效或ST部署。
- **文档纠正与工程绑定**：默认Modbus502、FINS9600、OPC UA需激活/公开变量；UA在CPU STOP后可继续服务。M8000只读且不在默认M区映射。归档19个BESS变量均私有、无软元件绑定，原D0–D17 CSV不能当现场映射。见[研究13](../research/firmware/13-documented-control-routes-2026-10-18.md)。
- **无界面通信/下载研究**：实际追通IH530工厂注册与GetPLCRunMode代码链；匹配PDB解出本地变量服务结构，但未建立直接CPU控制。真实历史下载为18个文件成功项，找到31处DownloadFile调用；本地仿真器32条命令映射与IH530同值常量存在语义冲突，未向设备试发。完整初始化、枚举、封装/提交和字节校验仍缺。见[研究12](../research/firmware/12-interface-control-2026-10-18.md)与[研究14](../research/firmware/14-easy521-download-paths-2026-10-18.md)。
- **现场只读观察**：22:41（上海）Realtek物理网口Disconnected/0bps；USB为8-3/0471:a531/H5U，旧6-3已是输入设备。没有沿用旧定位写入、扫描猜测IP或执行PLC协议请求。没有重新观察24V、RUN灯或当前CPU状态；未证实USB-only是RUN拒绝唯一根因。
- **验证**：现有tools unittest discover **68项通过**，含新增14项实际回环报文测试；只读monitor **82项通过**，两组不重叠共150项。独立审计、CLI纯预览、手册SHA256复算、JSON/本地链接与diff检查通过。原始结果见[本轮验证](../../evidence/easy521-control-20261018/software-validation.json)。这批是有针对性的本地回归，未重跑完整源码套件；远程CI随提交核验。

本轮未下载、RUN/STOP、操作物理输出或替换固件。准确SoC/OS/BSP仍未确认；T07/T08/T09/R01/R02继续开放。新工具须在取得真实IP、服务和地址用途后进行硬件验收；未因模拟服务器通过而标记Easy521控制完成。进展同步PR #13与总跟踪 #12，未合并默认分支、未启用后台持续研发。

## 2026-10-18：自主推进普通 ST 无界面对象编译与部署候选追溯

用户要求继续工作。三个 AI 代理并行实施，统筹独立复核实际编译和工作副本；[交付报告](HOST_TOOLCHAIN_DELIVERY_2026-10-18.md) 与 [ADR-0004](../architecture/ADR-0004-vendor-build-release.md) 记录最终结果。

- **R01 编译推进**：自写 MAIN 正文+显式 BOOL/INT/DINT/REAL 全局表，可独立准备厂商中间文件并调用 StLang，真实生成新 ARM 对象；+1/+2 的 `.text` 不同，重复编译字节一致，错误 ST 正确失败。独立混合类型/多语句测试最初发现编译器崩溃，补齐隐式 `_HC_EMPTYFUC` 声明后复测通过，原失败保留。仍缺运行库/地址/软元件/任务和全应用链接/封装，不声称已无 Windows 或已部署 PLC。
- **T06/T07 追溯修复**：构建清单补齐子目录覆盖，真实新 F7 清单覆盖 80 项输入、含 PLC 目录 5 个原遗漏文件。F8 设置确认前 108 个选定文件完全不变，未发生新编译；只点击取消。新增 intent/不可变 release、转换与独立 project/binary 等级，本机捕获/复验通过。确认后重编和设备字节绑定仍开放，没有重新下载、RUN 或 STOP。
- **T05 诊断/分发**：doctor 分列七种能力、缺失条件及真实 worker argv；磁盘/窗口/PLC 版本不混用，默认只读本地文件，不将历史成功填成当前状态。两 Python worker 与 PS helper 已随 wheel 分发，仓库外正文准备和诊断检查通过。
- **错误反馈**：修复 Windows ANSI 错误导致 Unicode traceback，以及编译错误数跨行误读下一行年份。非 UTF-8 错误后有副作用的动作不重放。
- **最终本地验证**：源码/监测 765 passed、51 skipped、1 deselected；工具 114 passed，另 499 个 subtest 通过，共 879 项不重叠测试通过。独立 sdist/wheel/全新 venv 检查通过；本机 Windows 编译实验另存 JSON。远程 CI 随新提交核验。

没有重新观察今天的设备供电或 CPU 状态。当前仍需完整应用链接/包装、确认后实际对象/设备读回及额定供电下的运行证据；T07/T08/T09/R01 保持开放。

## 2026-10-17：Easy521 真机应用下装、运行拒绝与独立编译研究

用户确认 USB-only、没有外接 24V、输出无负载、物理开关 RUN，RUN 灯灭。本轮完成真实应用下装，未把下装成功当作 CPU 运行成功；[设备报告](EASY521_APPLICATION_2026-10-17.md)、[CLI 用法](../EASY521_CLI.md) 与 [会话摘要](../../evidence/easy521-application-20261017/session-summary.json) 已记录。

- **T09 部分交付**：两个内存探针均实际经 USB 下载，下载后厂商独立校验一致。加 2 版编译后、下载前比较明确检出 ST 不一致。加 1 版两次 RUN 请求均拒绝，故障诊断数量为 0；未证明拒绝根因，未再 RUN。最终设备保留加 2 探针，原 BESS 备份保留，恢复未演练。
- **T07/T09 部署入口**：完整 verified 编译记录、精确工程、源/产物哈希、USB 双重身份、备份、现场事实、租约和严格证据接入 CLI；动作不自动重放。真实生产调用发出一次下载确认后返回 unknown，后续只读对账确定实际下载成功。厂商 F8/独立比较会重编译改变对象/删除生成文件，精确二进制追溯缺口仍保留，不能静默采用新哈希。
- **T08 部分交付**：只读 MSAA 从原生表格当前值列获取探针 12 个变量，真实 CLI 为 values_read=true；缺独立在线身份及有效状态位时 online/connection_verified=false、plc_state=unverified、退出 1。当前数据质量不等于真实扫描、鲜度或网口 Modbus 验收。
- **R01 新证据**：宿主 IPC 已可脱离 AutoShop GUI，把合法冻结 STTemp 容器用两个本机厂商 StLang 版本编成新 ARM `.o`。31 项 worker 测试与实际正负实验通过；lexer error 优先于伪 Success。普通文本预处理、完整链接/包装、独立设备协议尚未打通，仍依赖 Windows 编译器。见 [研究 09](../research/firmware/09-vendor-headless-interfaces-2026-10-17.md)、[研究 10](../research/firmware/10-stlang-headless-compiler-2026-10-17.md)。
- **供电结论**：厂商额定主电源为 24VDC ±10%；Type-C 可进行用户程序上传/下载。手册明确 USB-only 不支持掉电保持，未找到 USB-only 必定禁止 RUN 的明文，因此缺少 24V 不是已证实的唯一故障原因。

最终源码/监测/工具合并回归 **788 passed、51 skipped、1 deselected**，另 499 个 subtest 通过；与分项测试重叠，不相加。独立 sdist→wheel→全新 venv→仓库外软件场景与资源检查通过。具体结果见 `docs/management/evidence/easy521-software-validation-20261017.json` 与 `distribution-easy521-20261017.json`。适配器仅标定 AutoShop 4.12.0.2 的当前布局，未宣称其他型号、无 Windows 完整设备闭环、物理 I/O 或工业实时性通过。三个 AI 技术代理分别实施部署、CLI/监测、编译器研究；统筹代理独占设备操作并复核真实正负结果、分发和 GitHub。T09/T08/R01 保持开放。

## 2026-10-17：通用输入输出与 AI 一键验收交付

用户确认当前只有 Easy521，优先完善 WSL/Linux 中可由 AI 直接创建、修改、编译与验证的工作流。[交付报告](AI_WORKFLOW_DELIVERY_2026-10-17.md) 与 [操作说明](../AI_PROGRAMMING.md) 已补齐。

- 新 `open-linux-signals-v1` 支持最多 64 个 BOOL/INT/DINT/UINT/UDINT/REAL 直接 MAIN 字段。真正生成的类型与契约核对；旧 counter 工程和 release 保留兼容路径。
- `new --template motor` 带 ST、manifest、场景与 AGENTS.md；`verify PATH --json` 实际编译、部署、确定扫描执行并断言，失败关联 expected/actual 与场景/步骤/扫描。
- motor 6 场景/56 断言、analog 3 场景/16 断言通过；60→100ms 规则变化先被旧测试检出，新边界 t80ms 停/t100ms 起动真实通过；7/60/1000ms 周期均实测。
- 默认软件 535 passed、51 skipped、1 deselected；启用真实工具链的后端与验收套件 192 passed，与默认套件重叠。独立 wheel 的仓库外新建/实际编译/验收通过。中断后的进程清理和 CLI 结构化退出 130 已覆盖。
- 由三个 AI 技术代理并行实现，统筹代理做 CLI、独立正负联测、打包复核、CI 与 GitHub 同步。T13/#17 交付待合并，T12 仍只有本机语义基础；没有新 Windows/PLC 操作。

开放软件运行结果不替代 Easy521 设备运行。T09 的应用下载/运行、R01/R02 的厂商协议与系统固件研究继续单独跟踪。定期 GitHub 报告需合并默认分支后启用。

## 2026-10-17：三路线并行实施，开放 ST 首个闭环交付

用户确认三路线同时推进，以路线 2 优先。[本轮交付报告](PARALLEL_DELIVERY_2026-10-17.md) 记录代码入口、命令、实测证据及剩余工作。

- 路线 2：统一 CLI 已接入 `open-linux`，真实 ST 经 MatIEC/C/GCC 执行；计数、心跳、停止、完成、修改源码产生不同行为，以及改包拒绝均通过。默认工具链已在本机缓存验证，完整 Beremiz、远程板卡与物理 I/O 尚未接入。
- 路线 1：交付标准库应用产物/差分检查工具，实际 12 文件与 6 组对照，发现 Output.prg/sdt 不可单独绑定本次逻辑；完整 ABI/下载仍开放。
- 路线 3：交付有界 ELF/uImage/DTB/平台标识检查器与硬件证据表，35 项测试通过；实际设备镜像仍未取得，准确 SoC/OS/BSP 仍 unknown。
- 验证：默认源码套件 396 passed、13 skipped、1 deselected；实际工具链下后端套件 38 passed（与默认套件有重叠）；工具/汇报 54 项；最终 CLI 正负验收与独立分发通过。新增 CI 原生 ST job。没有新 Windows/USB/PLC 操作。

T11 已交付待合并；T10 部分交付；T12 与 R01/R02 剩余验收保持开放。新结果不替代 Easy521 实际下载、运行、I/O 或硬件周期验收。

## 2026-10-17：原生工具链与多 PLC 范围扩展

用户明确要求探索绕过 Windows AutoShop，并支持多种 PLC。已采纳 [ADR-0002](../architecture/ADR-0002-native-multitarget.md) 的双路线：保留厂商运行时的兼容研究与自控开放运行时并行；已有桥接保留为对照和兼容路径。

- **新增离线实验**：[Linux LLD 部分链接](../research/firmware/06-linux-link-experiment-2026-10-17.md) 成功处理本轮 ARM 对象；完整三库组合因重复符号失败，排除重复占位对象后可部分链接，仍有 46 个未解析名字。没有完成 LiteST 前端、完整 ABI、包装、下载或运行兼容。
- **新增本机证据**：[工具链台账](../research/firmware/05-local-toolchain-2026-10-17.md) 区分 Windows ST 前端、ARM 目标库及宿主 x86-64 Linux 库；本机附带说明提及 Linux 烧录库，但没有取得对应主库或验证 Easy521 适用性。未复制厂商原库、接口文档或定义到公开仓库。
- **新增官方来源**：[公开来源复核](../research/firmware/04-official-sources-2026-10-17.md) 找到官方固件归档元数据，未下载系统镜像；公开的另一 Easy521 型号页面写明四核，不足以确认本机 SoC、OS、BSP 或 I/O。
- **任务与接口**：新增 M4 与 [T10 / #14](https://github.com/BUG423/other-plc-idf/issues/14)、[T11 / #15](https://github.com/BUG423/other-plc-idf/issues/15)、[T12 / #16](https://github.com/BUG423/other-plc-idf/issues/16)。分别落实目标/设备/传输解耦、无 Windows 的真实 ST 闭环、跨目标语义对照；均为待执行任务。R01/R02 继续追踪厂商 ABI/协议及板级/系统固件研究。
- **实际执行边界**：本轮仅公开资料、主机文件分析和原生 Linux 链接实验，没有 AutoShop/Windows 进程、USB、PLC 或固件操作，没有运行候选开放 PLC runtime。既有真机下载/运行与网口验收仍未完成。

执行角色为固件来源研究、可移植架构及编译器实验 AI 代理，由统筹交叉复核。新增 JSON、来源哈希、链接输入输出/重定位与预期失败证据已复核；这次不修改应用软件，持续集成结果随提交更新。

## 2026-10-17：双线接入后的实施与现场复核

最新报告：[LIVE_VALIDATION_2026-10-17.md](LIVE_VALIDATION_2026-10-17.md)。以下首轮审计记录保留为时间快照，当前能力以此更新为准。

- **T03 已交付待合并**：资源/依赖封装、独立 wheel、sdist 到全新 venv 的仓库外运行、三场景验证与 CI 检查。
- **T04 已交付待合并**：`new/init/check`、版本化 manifest、内存计数/心跳示例、真实导入表及有限静态检查。不会伪造原生工程。
- **T05 部分交付**：`doctor` 与若干命令的结构化 JSON、稳定错误输出、校准参数与版本声明透传；尚未实现全部命令统一协议或后端注册。
- **T06 部分交付且已实测**：已有工程 MAIN diff/读回/保存、`build --source` 的新产物与日志验证。真实负向错误定位行 26、退出 1；恢复正确源码后 verified、退出 0。自动变量/任务同步、全版本覆盖仍开放。
- **T07 基础交付**：BUSID 与 VID/PID 同时匹配、跨项目进程租约、严格证据、构建/计划哈希及篡改检查。新编译流程已使用租约/证据/清单；完整设备部署入口与端侧校验仍未贯穿。
- **T08 软件质量交付，现场受阻**：字序不明退出 3；原始值、质量、量程、契约哈希与操作者声明版本进入 JSONL。USB 查询确认 Easy521/6.11.0.1；Realtek 网卡最终仍 Disconnected/0 bps，没有真机 Modbus 成功证据。
- **T09 下载/运行尚未执行，备份已取得**：供电/网口灯、当前输出隔离及开关状态的核对未返回；本轮没有程序下载或 RUN/STOP、固件操作。首次取消上载入口后，已另建 Easy521/LiteST 工程完成 USB 上载，厂商日志明确成功，62 文件与本地 ZIP 哈希已留存，MAIN 读回与仓库一致；恢复演练未做。

原 Windows 工程 120 文件的收尾哈希均与快照一致；所有编译实验在另一个工作副本进行。当前交互窗口保留工作副本 MAIN，原源码已恢复并保存；现场适配失误与纠正均如实记入报告。

本地软件回归 **346 passed、1 deselected**（排除真实 USB 枚举用例），汇报脚本另 **5 项通过**，Windows 生产标题校验函数另 **10 项通过**；sdist→wheel→新 venv→仓库外三场景验证通过。分发证据与 GitHub CI 随本轮提交更新，见 PR 的检查结果及 `docs/management/evidence/`；不把源文件检查、虚拟场景或编译成功当作真机运行验收。

## 2026-10-17：基线审计与首轮修复

当前工作分支：`codex/plc-idf-baseline-20261017`。审计起点：`d87e5da`。

### 已确认

- 仓库是 PLC-IDF 原型，目标 Easy521。存在五个 CLI 子命令、Windows GUI 调用链、固定 BESS 寄存器监测、虚拟场景播放器及算法/AutoShop 历史工程资产。
- `init/check/sync/doctor`、通用用户 ST 仿真、机读构建诊断、产物清单、设备租约与下载后校验尚未完成。历史人工下载证据不能证明当前 CLI 自动闭环已验收。
- 原有 build/flash 根据 PowerShell 退出码判成功，必需校验未实现。已交由实现代理修复“未知结果误报成功”、配置类型与 GUI 重试；最终结果见下方验证记录。
- 现有厂商目标文件提供 ARMv7 应用目标和厂商 ABI 线索。芯片具体型号、板上系统、启动链及 I/O 驱动仍不能据此确定。详见 [离线与公开资料评估](../research/firmware/03-feasibility-assessment-2026-10-17.md)。

### 技术分工与交付

| 技术执行角色 | 本轮工作 | 交付 |
|---|---|---|
| 架构审计 AI 代理 | 第一性原理、设计与实现差距、规范勘误 | [架构评审](../reviews/ARCHITECTURE_REVIEW_2026-10-17.md)、SPEC/ARCH 注记 |
| 实现与测试 AI 代理 | 软件回归、缺陷复现、有界 P0 修复 | [实现审计](../reviews/IMPLEMENTATION_AUDIT_2026-10-17.md)、src 修复和测试 |
| 固件研究 AI 代理 | 官方资料与本地 ELF 分析 | [研究评估](../research/firmware/03-feasibility-assessment-2026-10-17.md)、离线样本哈希与输出 |
| 统筹代理 | 交叉复核、需求/任务、CI、GitHub 同步 | [实施计划](PLAN.md)、任务 Issue、进度机制 |

### 验证记录

修复前：CLI 80 passed，监测工具 65 passed。CLI 基线套件中一个测试执行了 `usbipd list` 只读枚举；未进行 attach/detach、GUI 驱动、串口通信、PLC 下载或运行控制。后续软件验证明确排除该测试。

修复后独立集成复核：`.venv/bin/python -m pytest src/plc_idf/tests wsl-tools/tests -q -k 'not test_devices_real_readonly'` 退出 0；对应 CLI 95 项 + 监测工具 65 项，共 160 项通过、1 项排除。进展脚本另有 5 项离线测试，覆盖人工正文不被覆写、事件防循环和汇总渲染。`git diff --check`、新增 JSON、本地文档链接与四份 ELF 样本 SHA-256 检查通过。

三项 P0 修复已落地：授权配置仅接受布尔 true；改变状态的 GUI 操作不自动重试；必需校验未实现时返回 unverified、ok=false 和退出 1。退出 1 不能推出设备未改变，亦不能据此自动重跑。真实编译/下载验证、目标身份与互斥仍由 T06/T07/T09 继续实施。

### GitHub 同步与汇报状态

- [总跟踪 Issue #12](https://github.com/BUG423/other-plc-idf/issues/12)。已建立 11 个任务 Issue（#1–#11）与 M0/M1/M2/M3/RX 五个里程碑；逐项链接在 [tasks.json](tasks.json)。T01/T02 为本轮交付，待代码合并；其余任务开放，研究任务已有首轮证据，尚无原生后端原型验收。
- `software-tests.yml` 提供源码树软件 CI，独立 wheel 验收仍为 T03 未完成项。
- `progress-report.yml` 提供任务变动、手动触发和每周一 09:00（Asia/Shanghai）的元数据报告；定时触发可能延迟。报告以独立评论追加到总跟踪 Issue，避免覆盖人工编辑；脚本已用真实 GitHub API 发出首份报告。
- 工作流当前在本工作分支提交，**尚未合并默认分支，因此定期工作流未启用**。本轮没有创建 ChatGPT 后台任务，没有持续研发代理；已发出的报告不代表未来会自动完成技术任务。

### 后续依赖与边界

- 本轮完成范围是 M0、首轮研究及 CI/报告机制的准备；不代表整个 PLC 工具链已交付。
- M1：独立安装与依赖封装、工程模型和检查、能力/JSON 接口。
- M2：正确/错误编译的真实 Windows 证据、源码同步、产物/部署契约和设备互斥。
- M3：网口可达、现场工程映射、实验机身份、逐次下载/运行操作授权和用户在场。
- 没有持续运行的研发代理。GitHub 定期报告只根据任务状态生成摘要；工作流在默认分支启用后运行，不能替代新的测试和硬件验收。
