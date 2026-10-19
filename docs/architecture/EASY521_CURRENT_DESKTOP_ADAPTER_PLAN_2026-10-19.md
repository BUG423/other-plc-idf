# Easy521 当前桌面适配器增量计划

2026-10-19。本文仅提出下一批软件改动和验收范围，**本轮不改适配器或 PowerShell，不追加设备写入**。统筹优先保存本次灯程序的真实编译、下载、比较、CPU 状态和观察证据。

## 当前事实与能力边界

现有 [autoshop_vendor.py](../../src/plc_idf/autoshop_vendor.py) 只接受 AutoShop 4.12.0.2 的一个桌面配置：主 frame `(-16,-16,3872,2096)`、屏幕 `3840×2160`、App DPI 96、System DPI 192。`_layout()` 在 preflight、download 和 compare 中均检查这个配置；比较使用两个固定点击点 `(520,86)`、`(600,864)`。

本轮统筹实际操作的配置为屏幕 `1920×1080`、App/System DPI 均为 96、主 frame `(-8,-8,1936,1048)`。后两个矩形数值是宽、高，不是右、下坐标。PLC 菜单点 `(253,35)`、程序校验菜单点 `(300,433)` 来自本次桌面观察和统筹操作，不是旧坐标缩放。

**1920 配置目前只有统筹直接调用 UI 原语的实测证据，现 CLI 的受限适配器仍应拒绝它。** 厂商下载/程序一致性日志通过，不能改写为 `AutoShopVendorAdapter` 的端到端成功，也不能凭 mock 测试把新布局标成已完成真机验收。本轮下装意图已明确标注此区别：

- [led-download-intent.json](../../evidence/easy521-power-20261019T031500Z/led-download-intent.json)：`adapter_scope` 为 root supervised semantic dialog automation，不是现适配器布局验收。
- [本次 diagnostics](../../evidence/easy521-power-20261019T031500Z/032256922020-ui-diagnostics-fa62b3.json)：屏幕、DPI 和前台进程。
- [PLC 菜单桌面截图记录](../../evidence/easy521-power-20261019T031500Z/033847635149-ui-desktop-c9a020.json)：本次菜单观察来源。

CPU RUN/STOP、文件传输、工程一致性、设备字节一致性及灯的光学现象继续分开记录。本文不把面板观察升级为设备二进制回读。

## 1. 新增独立布局配置，不撤销旧守卫

在 `autoshop_vendor.py` 以固定白名单保存两个布局配置。每项同时包含版本、frame 矩形、屏幕尺寸、App DPI、System DPI、PLC 菜单点、程序校验点以及校准证据来源。禁止从命令行传任意像素，禁止按 DPI 或分辨率插值。

| 配置 | 主 frame：x,y,width,height | 屏幕 | App/System DPI | PLC / 校验点 |
|---|---|---|---|---|
| 原 3840 配置 | `-16,-16,3872,2096` | `3840×2160` | `96/192` | `520,86` / `600,864` |
| 新 1920 配置 | `-8,-8,1936,1048` | `1920×1080` | `96/96` | `253,35` / `300,433` |

一次观察必须完整且唯一匹配同一配置，不允许拿旧 frame、1920 屏幕和另一 DPI 混搭。保留精确工程路径、MAIN 文档、版本、已保存/可写编辑器、PID、前台主窗口、唯一主 frame、无未知 modal 和严格 USB BUSID+VID:PID 检查。编辑器可写表示退出了在线监控，**不表示 CPU 已 STOP**。

`_layout()` 返回匹配配置 ID/坐标，调用方使用返回配置而不是另取默认常量。preflight 后将配置 ID 绑定本次 session；两次菜单点击之间再次核对，布局变化、焦点转移、未知 modal 或进程替换时拒绝下一步，不重新选配置后继续点击。未知布局仍为 unavailable/unknown，不发送输入。

主菜单是自绘控件，现有代码没有稳定语义 command ID。新配置仍只能支持这一受限桌面状态。菜单项排序、菜单是否展开、缩放/多屏、窗口状态改变都可能影响坐标；后续验收必须保留点击前后 inspect/diagnostics 和菜单图，不能仅证明 frame 大小相同。

## 2. 下载流程改为有限的对话框状态处理

当前 `download()` 确认“下载设置”后只轮询日志。RUN 中下载会被停止确认 modal 阻塞，因此当前实现可能超时；它已经将 `download_called=true` 标为不可重放，这是应继续保留的行为。

本轮新出现了两个不同正文，不能通过同名“AutoShop”窗口或通用“确定”处理：

| 阶段 | 完整正文 | 默认处理方向 |
|---|---|---|
| 下载设置确认后、传输前 | `PLC处于运行状态，确定要将PLC停止并且继续操作吗？` | 只有本次下载计划允许为下载停止 CPU 时，单次确认 |
| 下载完成后 | `PLC处于停止状态，确定要将PLC运行并且继续操作吗？` | 当前 `auto_run=false` 路径单次取消，RUN 另行处理 |

实际证据分别为 [停止提示](../../evidence/easy521-power-20261019T031500Z/033739551405-ui-inspect-2ed527.json)、[停止确认意图](../../evidence/easy521-power-20261019T031500Z/033748926523-ui-button-a1767c-intent.json)、[运行提示](../../evidence/easy521-power-20261019T031500Z/033758239502-ui-inspect-6d75c5.json)和[统筹运行确认意图](../../evidence/easy521-power-20261019T031500Z/033811366064-ui-button-6407c0-intent.json)。最后一项是统筹本次操作事实，不是适配器已有能力。

建议有限次序：

1. 一次 F8，取得并严格校验下载设置，捕获/验证 prepared release，确认设置一次。
2. 若出现上述精确停止提示，校验所属 PID、工程、唯一可见 modal、完整 Static 正文和唯一 enabled push button。本次计划允许停止时仅确认一次；否则保留待处理状态，不猜测或自动改变 CPU 模式。此软件计划取自已有会话授权和现场事实，不另造用户 token 或重复审批。
3. 继续读取新鲜传输日志；只接受本次开始、必需文件成功和最终成功。停止提示消失不代表程序已下装，按键投递成功不代表 CPU 已切换状态。
4. 下载终态与可能随后出现的 RUN 提示分别处理。已知 RUN 提示按默认策略取消；未知/重复/矛盾 modal 保留 unknown，不通用确认，也不擅自关闭未知窗口。
5. 全程每项输入都有不可覆盖的意图/结果证据和共享 deadline；F8、设置确认、停止确认、RUN 提示取消各最多一次。超时/焦点丢失/结果未知后不自动重试整条流程或重放下载。

允许 STOP 中下载没有停止提示，保留旧分支兼容。不要要求“必须出现一个停止提示”才能承认正常传输。反之，同一操作第二次出现停止提示或设置窗口，应当视为未校准分支，而不是再次点确定。

## 3. RUN 提示策略与 CPU 动作分离

当前 [native_deploy.py](../../src/plc_idf/native_deploy.py) 的 `_observation()` 明确拒绝 `run_requested=true`，部署上下文固定 `auto_run=false`。保留这项合同：本次 download 的默认结尾是“拒绝启动提示”，而不是“恢复下载前 RUN 状态”。

建议下载结果新增结构化提示处理事实，例如 `run_prompt.observed/decision/action_posted` 和 `stop_requested`，并保留 `run_requested=false`。若在本次下载中确认过停止提示，部署摘要不能仍无条件声称 `stop_requested=false`；编排层需要保留这项实际动作事实。CPU 实际状态在没有独立读回时仍为 unknown，不能把“确认停止/取消运行”直接写成 STOP 已验证。

用户已经授权灯演示的本次统筹 RUN 可以作为独立 runtime 步骤记录，但不借此扩大适配器通用下载能力。未来如要新增 runtime API，应单独设计输入、一次执行、状态核验和结果字段，不向现 download 偷加自动 RUN，也不把 `run_requested=true` 的观察传进现有部署合同后强行放行。

本轮固定 GCM 状态读回属于另一条已实测路径。AutoShop 可能独占 USB，不能在 GUI 正在持有设备时无条件混用独立 libusb 查询，更不能为查询自动关闭工程/进程。下一批决定独立 runtime observer 如何获取租约和释放通信所有权之前，适配器只报告可取得的证据；没有核验时不构造状态成功。

## 4. 继续保持 release 和日志追溯

本次新布局和提示处理不改变 `native_release` 的完整性规则：用户选定 build 是 intent，确认前捕获的 release 不可变；下载后源码、配置或产物漂移仍须按既有规则记录。独立 compare 引起重编时，project-consistency 和 binary-consistency 继续分级，不能更换 release 哈希使结果变绿。

“下载设置确定”和“停止并继续确定”应各记录 Windows 时间。若出现停止提示，实际传输动作的 freshness 边界应绑定最后一次触发传输的确认，保留之前时间作为过程证据；若无提示则使用设置确认边界。保留现有 append-only/通讯 pane+Windows 时窗规则和秒级歧义拒绝，不因为多了 modal 而接受旧成功日志。RUN 提示的后续时间不能倒用为下载开始边界，避免排除已经完成的真实传输记录。

下载成功日志、RUN 提示取消和独立工程校验是三个结果。某一步失败时仍保留已经发生的下载事实，不能声称设备未变，也不能用重复部署解决不确定性。

## 5. 下一批具体文件与测试

优先改动范围：

- `src/plc_idf/autoshop_vendor.py`：固定双布局选择、session 布局绑定、精确 modal 校验和有限下载状态处理；默认拒绝 RUN 提示。
- `src/plc_idf/tests/test_autoshop_vendor.py`：扩展纯 mock 状态序列，保留原拒绝条件；mock 只验证软件逻辑。
- `src/plc_idf/native_deploy.py` 及专属测试：仅在需要时透传真实 `stop_requested`/提示结果，保持 RUN 与应用部署分离。该变更与 CLI 代理协调，不能让摘要继续显示未请求 STOP。
- `plc/tools/autoshop_ui.ps1`：现 `Get-ExactDialog` 的 `ExpectedText` 使用 `Contains`。Python 应先对单个 Static 的首尾空白做规范化后全文相等校验；如需消除读取和按键间的 modal 替换风险，再由统筹增加 `ExpectedHandle` 和精确文本模式，动作时同时复核。不要直接把所有旧调用的 Contains 改为不同语义。

必须覆盖的纯离线测试：

1. 原 3840 配置继续通过；1920 完整配置选择新坐标；混合 frame/DPI/screen、窗口非最大化几何、不同版本/项目、焦点变化拒绝且无输入。
2. 两次菜单点击之间布局/项目/进程或 modal 变化，不发送第二次点击；新配置不靠简单 0.5 缩放。
3. STOP 中直接下载；RUN 中出现停止提示，按已授权计划单次确认后传输；计划不允许停止时不确认；同名错误窗口、额外文字、重复 Static/按钮、错误 button type、第二次提示均拒绝。
4. 设置确认或停止确认的超时/错误/输出未知，不再 F8、不再确认、不重放下载；已保留的 intent/release 和动作证据不能覆盖。
5. 传输成功后已知 RUN 提示默认取消，`run_requested=false`；未知提示不点击；提示取消失败保留实际下载终态并返回 unknown；无提示保持旧分支。
6. 日志 pane 切换、过期/同秒歧义/缺少文件成功、新日志时间倒退仍拒绝；新状态机不复用旧终态。
7. 原 intent/release 漂移、compare 重编、证据落盘失败继续拒绝；摘要正确区分请求 STOP、设备实际状态、下载、project compare 和 binary unknown。

后续真实验收必须由统筹另排一个已有用户授权的实际需要场景，**使用适配器本身**走完整调用链，并保存版本/布局/源/build/release/目标身份及不可变动作记录。验收分别给出原语校准、mock 通过、adapter 真机通过三种等级。布局支持代码和 fixture 测试不能自动得到最后一级。

本轮不为完成此计划再次下载、切换模式、调整坐标或重演已成功灯试验；已有实测证据按原样保存，软件现状仍是受限旧适配器。
