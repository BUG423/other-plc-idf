# Easy521 接入 24 V 后的读回与 Y0 台架实验（2026-10-19）

证据目录为 [easy521-power-20261019T031500Z](../../../evidence/easy521-power-20261019T031500Z/)。本轮已完成灯程序的完整厂商编译、USB下装和厂商程序比较；用户明确确认输出区Y0闪烁、STOP后熄灭。独立USB读回覆盖RUN、STOP及恢复RUN，时钟也出现变化。下文保留初稿审阅与随后统筹实测的边界。**当前流程仍依赖Windows编译器、驱动和受监督桌面操作；设备字节身份、精确任务周期及负载电气性能没有验收。**

## 现场范围与此前应用

- 用户报告已接入 24 V，PWR 及其称为 WAN 的灯亮，随后明确所有输出端悬空、没有负载。保留用户原称谓，不把 WAN 推成 RUN；物理开关当前位置没有重新目视确认。见 [field-facts.json](../../../evidence/easy521-power-20261019T031500Z/field-facts.json)。
- 本轮重新枚举的 USB 定位为 `7-2`、`0471:a531`，是连接定位信息，不能代替唯一设备序列号或固件身份。旧 `6-3` 本轮为其他设备，见 [位置更正](../../../evidence/easy521-power-20261019T031500Z/local-device-location-update.json)。
- 下载 LED 前的 11 次固定 GCM 只读查询均返回 raw `1`，依[报告 18](18-runtime-mode-correction-2026-10-19.md)的厂商调用/UI 静态映射解释为 RUN；`device_calibrated=false`。单凭该状态不证明应用扫描推进，也不能拿它当作下载后的状态。见 [初始轮询汇总](../../../evidence/easy521-power-20261019T031500Z/initial-poll-summary.json)。
- 新鲜 F9 上传成功后，工作副本 MAIN 正文经行结束规范化与既有 `+2` 探针一致，源码 SHA-256 为 `392ae08c3e1e2bbed430af326d287e042bf15d3dc8f674712a6ed748f40acc09`。这是上传源码核对，不是设备应用二进制比对。见 [uploaded-main-check.json](../../../evidence/easy521-power-20261019T031500Z/uploaded-main-check.json)。
- 上传后 ZIP 备份 SHA-256 为 `0f30c3f0e63a61fd1776c4cbb29d646d5bbd33f01ca025a1da505a2ba562ab71`，独立复核一致。它含源码、工程与既有主机产物，不是独立提取并验证的设备镜像；尚未恢复演练。见 [备份记录](../../../evidence/easy521-power-20261019T031500Z/powered-upload-backup.json)。

## ReadMemoryEx 首次真机读回

[固定 revision 读取](../../../evidence/easy521-power-20261019T031500Z/memory-revision-first.json)的 22 字节请求收到真实 `op=2`、26 字节响应，序号、计数、地址和位数对应，REAL32 数据 `00864b47` 解码为 `52102.0`，与新上传的探针标记一致。后续六次固定项读取也成功：

| 顺序 | 固定项 | 值 |
|---|---|---:|
| 1 | scan-counter | 9602 |
| 2 | confidence | 19674.0 |
| 3 | published-scan | 0.0 |
| 4 | published-value | 400.0 |
| 5 | heartbeat | true |
| 6 | scan-counter | 735 |

独立核对七次记录的帧长、序号与 echo、各一次 22 字节发送及 claim/release/close 返回值，均符合本次固定读契约；helper 哈希与首次记录的代码文件哈希一致。它们将[报告 19](19-existing-variable-read-2026-10-19.md)的相应固定请求从静态候选推进为本机实测通信证据，不外推为任意地址或其他机型协议。

`9602→735` 与已上传源码的 `1..9999` 计数范围及回绕兼容，结合 revision 标记支持该窗口内既有探针扫描推进。不能由两点推算精确任务周期、唯一回绕次数或完整设备二进制身份。各变量跨时刻逐项读取，可能跨越回绕；`Confidence` 与两个 published 值不是同一扫描的原子快照，未用它们作同一扫描不变量比较。见 [powered-probe-runtime-observation.json](../../../evidence/easy521-power-20261019T031500Z/powered-probe-runtime-observation.json)。这些是 LED 下载前的旧探针证据，不能证明新 LED 应用执行。

## LED 源码与完整厂商编译

使用 [MAIN_body.st](../../../examples/easy521_led_blink/MAIN_body.st)，SHA-256 为 `6c51789d485457f9fc64ff34c2f0101f6c10db61bce1bd2a0f529c2c0111e7ed`。正文首个 RUN 扫描令 Y0 为 FALSE，后续令 Y0 跟随 M8013；沿用现有任务、变量与配置，不新增任务/定时器。Y0 是真实物理输出命令。M8013 的手册周期为完整 1 秒，不保证精确占空比、启动相位或实测任务周期；STOP 后正文不再执行。手册来源、哈希及物理 I/O 边界见[示例说明](../../../examples/easy521_led_blink/README.md)。

工作副本为 `E:\PLCProjects\PLCIDF_LED_20261019T031904Z\BESS_E521_DEV`。WSL 命令调用 Windows AutoShop 4.12.0.2 的工程同步和 F7 完整编译，直接软元件经厂商预处理。此结果不是 Linux 原生完整编译，也不是受限 ST 对象编译或 Linux LLD 链接的替代验收。

### 必须保留的冻结错误与真实编译失败

1. 11:31:44–45 首次完整编译零错误，记录为 [led-build-record.json](../../../evidence/easy521-power-20261019T031500Z/led-build-record.json)，build hash `d904440333d0c807df1fec826d03a346a6af1ea47d7033e659f777a5ed21b2a8`。
2. [首次候选冻结记录](../../../evidence/easy521-power-20261019T031500Z/led-compiled-candidate.json)误将绝对产物路径直接拼接私有目录，绝对路径丢弃了目标前缀，实际重写了原产物相同字节，并将原文件权限改成只读。**这次尝试不是有效私有冻结，不能继续用其目录或只读属性证明冻结成功。**
3. [修正记录](../../../evidence/easy521-power-20261019T031500Z/led-compiled-candidate-corrected.json)保留错误，以 `relative_to(project_root)` 构造私有目标路径，恢复原文件可写权限，另存真正的五项副本。独立核对路径确在修正私有目录内，副本哈希均与首次编译相符。
4. F8 引发的 11:33:53–58 前置重编译实际失败：`Output.prg` 打开失败，统计 **1 错误**，随后设置窗口消失。见 [03:36 读取的原始日志](../../../evidence/easy521-power-20261019T031500Z/033632365590-ui-log-data-3b4a2b.json)。此时没有开始下载/成功日志，统筹尚未点确认；失败时段与误设只读时段一致。不能因文件字节未变或后续权限恢复而把该次编译当成功。
5. [Windows 属性复核](../../../evidence/easy521-power-20261019T031500Z/033647795859-candidate-file-attributes-2bc6bb.json)确认五项均为 `Archive`、`read_only=false`，哈希仍一致。随后重新显式构建，11:36:58–59 零错误，形成 [led-build-record-after-permission-correction.json](../../../evidence/easy521-power-20261019T031500Z/led-build-record-after-permission-correction.json)。本轮下载意图绑定此新构建，不沿用失败前的成功日志作新下载验收。

新构建记录 SHA-256 为 `23be48478018e944c62f5ceb4fabb7acb929713115e6f2e69a8b4e0e2fea2dda`，build hash 为 `4d92b527829f8ca570d28127e6d080b8d5733a1af8d97fa136a9813946a657f8`，输入树 SHA-256 为 `65fe86d4cc2a8c0b6c4c90c31f0cee8c0551b48209ed481e5b805224471757cf`。两次成功构建的 manifest 仅创建时间及随之计算的 build hash 不同，五项产物与输入树相同；独立重算两份 manifest hash 均一致。

## 下载前绑定与当前验收边界

[03:37:37 下载意图](../../../evidence/easy521-power-20261019T031500Z/led-download-intent.json)绑定上述新 build hash、LED 源码哈希、准确工作副本和 USB 定位，并新存 `.plcidf/led-release-20261019T033737Z/` 五项只读副本。独立复核新 release 与修正冻结的文件内容均对应下表：

| 产物 | 字节 | SHA-256 |
|---|---:|---|
| Output.prg | 56 | `3bfe2324eba14f55199d5bb4d0ee72d265a7453fc78b0c6e66d265ae21adb63b` |
| Output.sdt | 182 | `c07a72afae6f4cac3e87308bab17d0eec1658a764b7e67102de77ec984ca863b` |
| STTemp/MAIN.o | 2200 | `d7703f4c9b4d76227a69474e913d4c0885181d866ee5e89b218c4795aadb5994` |
| STTemp/MAIN.rst | 10 | `80587217fc582ebb6cba647bb021e9a749f89a0474642deccbf1e04c4991dac7` |
| Temp/stprog.o | 125812 | `51d0174a3c6faf0f219c5e15e54199ab3a75ed089e0673c826b4491b545e9886` |

这是选定构建产物的本地绑定，不是完整线上传输闭包的字节回读。下载意图自身的 `outcome=not-started` 是确认前快照，不能把它改写成成功，也不能用它否认之后另存的日志。

[11:37:51–52 厂商通讯日志](../../../evidence/easy521-power-20261019T031500Z/033752306315-ui-log-data-7d89a4.json)已记录“开始下载”、各文件成功及“下载成功”。这是当前可确认的传输日志事实；按钮调用回执本身 `outcome_verified=false`，不能单独证明成功。本轮为统筹监督的语义对话框操作，不是既有 AutoShopVendorAdapter 屏幕布局的验收。未进行系统固件替换。

本报告复核时，原工作目录 `STTemp/MAIN.o` 已为 SHA-256 `8daf095e4d1d73f37abd4fe418e983ffda6b1c0c21f3da8b18bd94a1b44db42d`（2200 字节），`Temp/stprog.o` 已为 `69f3e60db59df3eae06cc96d9ad3716a08c114b8ffcb3a3bc369a56510b17fff`（125848 字节）；与 release 不同。[下载后主机产物记录](../../../evidence/easy521-power-20261019T031500Z/led-post-download-artifacts.json)保留该漂移。私有 release 五项仍保持表内值。记录漂移，不自动采纳新工作产物，也不据此声称设备收到了任一组确切字节；后续编译/比较造成的变化须另存来源。

11:37:58 界面提示“PLC处于停止状态，确定要将PLC运行并且继续操作吗？”，统筹于 11:38:11 对准确提示确认一次；[回执](../../../evidence/easy521-power-20261019T031500Z/033811366064-ui-button-6407c0.json)仍为 `outcome_verified=false`，表示调用发起，不是独立 RUN 读回。随后对本次桌面截图校准菜单执行程序比较，11:39:01 的[新通讯日志](../../../evidence/easy521-power-20261019T031500Z/033902005562-ui-log-data-f40777.json)和 11:39:19 的[准确工程对话框](../../../evidence/easy521-power-20261019T031500Z/033919989347-ui-dialog-90e2c2.json)均明确“当前工程与PLC中程序校验一致！”。这支持厂商比较通过；厂商校验算法与覆盖范围未独立解析，仍不是完整设备字节回读。

| 初稿时的后续验收项（截至03:39:19 UTC） | 当时事实；后续结果见下节 |
|---|---|
| 下载传输 | 已有厂商成功日志及下载后主机产物记录；设备完整字节身份未验证 |
| 程序一致性比较 | 厂商日志和准确工程对话框均通过；不提升为独立设备二进制 SHA-256 验证 |
| LED 下载后的 CPU 状态 | 待填：独立状态查询或厂商在线状态；旧探针 raw1 不代替本轮结果 |
| Y0 视觉与周期 | 待填：用户实际观察、准确灯位和时间/视频；不伪造人工反馈，不从源码推成灯已闪 |
| STOP 与收尾 | 待填：模式操作结果、独立状态、灯行为及最终保留/恢复状态 |

本次独立审阅仅离线读取既有证据、源码与本地文件哈希并撰写此报告，未调用 Windows、USB 或 PLC，未重跑测试。真机执行由统筹独占；原始失败、历史记录及私有厂商产物不被本报告覆盖或公开提交。

## 统筹后续实测：RUN、时钟和准确Y0观察

正常关闭精确实验工程后，AutoShop留下无窗口进程。统筹只在PID、启动时间、exe路径、主窗口为空且没有该PID顶层窗口全部吻合时清理本轮自建进程；没有为读取而关闭未知用户工程。清理后独立worker通过各自设备租约进行查询，没有与厂商GUI同时占用USB。

- [首次下载后GCM](../../../evidence/easy521-power-20261019T031500Z/led-gcm-first.json)返回raw1；[M8000](../../../evidence/easy521-power-20261019T031500Z/led-m8000.json)为true。
- [六次M8013样本](../../../evidence/easy521-power-20261019T031500Z/led-clock-sequence.json)为`true,true,false,false,true,true`，各响应均精确匹配固定请求。它证明采样窗口中该时钟位出现变化，不是精确周期、占空比或每扫描时间测量。
- 用户先报告指示灯明显闪烁，随后明确补充“输出区的Y0闪烁”。两条原意分别保存在[初次视觉反馈](../../../evidence/easy521-power-20261019T031500Z/human-visible-blink.json)与[准确Y0确认](../../../evidence/easy521-power-20261019T031500Z/human-y0-blink-confirmed.json)。后者补足灯位，未覆盖前者的不确定性。Y0未被加入任意内存地址读取，光学结论来自用户现场观察。
- [下载后轮询汇总](../../../evidence/easy521-power-20261019T031500Z/led-poll-summary.json)包含5次成功GCM读回，均raw1；随后为独占STOP/RUN测试显式暂停。此前还有下载前11次RUN样本。名义间隔以monotonic调度30秒，实际UTC起止均保留；不把主机时钟差异隐藏成精确定时验收。

## 显式STOP及恢复RUN

没有改写或重新下载灯应用。重新打开同一工作副本后，统筹按当次已确认的厂商菜单快捷键F6单次请求STOP，[11:44:44通讯日志](../../../evidence/easy521-power-20261019T031500Z/034445460547-ui-log-data-f659d6.json)报告“停止命令执行正确”。用户针对Y0是否熄灭明确答复“已经熄灭”，见[STOP人工反馈](../../../evidence/easy521-power-20261019T031500Z/human-y0-off-after-stop.json)。

再次退出本轮自建AutoShop、释放通信所有权后，独立读取三项；随后重新打开同一工程，F5单次请求RUN，[12:32:36通讯日志](../../../evidence/easy521-power-20261019T031500Z/043237730396-ui-log-data-160273.json)报告“运行命令执行正确”。关闭该自建进程后再次独立读取：

| 阶段 | GCM raw | M8000 | M8001 |
|---|---:|---|---|
| STOP后 | [0](../../../evidence/easy521-power-20261019T031500Z/led-after-stop-gcm.json) | [false](../../../evidence/easy521-power-20261019T031500Z/led-after-stop-m8000.json) | [true](../../../evidence/easy521-power-20261019T031500Z/led-after-stop-m8001.json) |
| 恢复RUN后 | [1](../../../evidence/easy521-power-20261019T031500Z/led-restored-run-gcm.json) | [true](../../../evidence/easy521-power-20261019T031500Z/led-restored-run-m8000.json) | [false](../../../evidence/easy521-power-20261019T031500Z/led-restored-run-m8001.json) |

STOP命令、实际读取及RUN恢复之间存在记录时间间隔，不描述成连续高速抓包。三项也为逐项读取，不构造原子状态快照。结合精确厂商命令结果与用户Y0反馈，本机本窗口取得RUN/STOP受控切换的一致证据；通用reader继续保留其静态/候选校准字段，不外推其他固件、型号或连接。

恢复RUN后另起[有界轮询计划](../../../evidence/easy521-power-20261019T031500Z/led-restored-poll-plan.json)：名义30秒、最多6次，仅GCM读取，任何失败停止且不重试。最终样本汇总和恢复后的视觉反馈按实际结果另存，不假装后台无限运行。当前保留灯应用与RUN；原BESS未恢复，上传ZIP未做恢复演练，所有输出仍按本轮用户声明无负载。

人工反馈的`observed_at=null`是用户未给出精确观察时间，采集工具的schema outcome因此保持unknown；这不抹去其明确的Y0闪烁/熄灭观察，也不将录入时间冒充观察时刻。源hash/build hash与附件关系均保存，设备二进制身份仍unverified。

## 软件交付和下一批任务

固定ReadMemoryEx工具新增离线codec、单次Windows驱动worker、默认纯预览以及147项正负测试。完整工具回归本轮498项收集，496通过、2项真实LLD条件未启用而跳过；这批软件结果与真机记录分别报告。新增资源同时纳入sdist/wheel，仓库外全新venv的受限preview和既有分发测试通过，详见[分发验收快照](../../../evidence/easy521-power-20261019T031500Z/distribution-validation.json)。源码/monitor回归结果另存软件验证记录，不重复叠加子测试数量。

后续将本次1920布局与双提示按[适配计划](../../architecture/EASY521_CURRENT_DESKTOP_ADAPTER_PLAN_2026-10-19.md)纳入严格CLI，保留现有3840守卫和不自动RUN合同。完整Linux原生路线继续拆解变量符号、配置包装和下载事务。T08的Modbus/故障质量、T09的精确周期/错误恢复/字节追溯及R01完整原生部署仍开放；不因可见灯成功而关闭整个项目。

## 本轮收尾

恢复RUN后的[6次轮询汇总](../../../evidence/easy521-power-20261019T031500Z/led-restored-poll-summary.json)全部成功且raw1，名义30秒采样结束；[收尾记录](../../../evidence/easy521-power-20261019T031500Z/led-session-completion.json)明确当前没有继续无限后台轮询。恢复RUN后的再次Y0目视反馈尚未收到，不把恢复前的闪烁确认重用成恢复后的新观察。后续回答应另存新记录。

[本轮软件验证](../../../evidence/easy521-power-20261019T031500Z/software-validation.json)：源码/monitor 770通过、51跳过、1真实Windows枚举排除；tools 496通过、2条件LLD测试跳过。分发专属9项和新内存工具147项是上述集合子集，不重复相加。隔离sdist→wheel→新venv验收通过。真机编译和设备操作另有独立记录，软件CI不能替代。

下一批严格桌面适配已建立[T15 / Issue #19](https://github.com/BUG423/other-plc-idf/issues/19)，明确责任、文件范围、负向测试和单独真机验收。任务状态同步使用main唯一分支；原系统固件没有替换。
