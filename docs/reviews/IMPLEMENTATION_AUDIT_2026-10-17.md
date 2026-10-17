# PLC-IDF 实现与质量审计（2026-10-17）

审计基线：`d87e5da` 的源码与本次未提交 P0 修复。范围为 `src/plc_idf`、`wsl-tools`、`plc/tools`、包配置和相应测试。结论来自本地源码及实际软件测试；本报告不构成 PLC 编译、下载或运行的现场验收。

## 1. 结论与当前真实能力

项目已具备能工作的命令行骨架、只读 Modbus 监测客户端、REAL32 契约与字序判别，以及回环 TCP 虚拟从站的软件闭环。`sim` 播放的是 Python 内置简化规则，未执行用户编写的 ST，也不等同于 PLC 运行时仿真。

`build` / `flash` 实现的是调用 Windows AutoShop 的 GUI 编排。它们没有 WSL 原生 ST 编译器，也没有原生下载器；必需的产物及设备结果校验仍未实现。本次修复使调用链即使全部返回进程退出码 0，最终也明确返回 `ok=False` 和 `verification_status=unverified`，避免将“命令调用完成”误作“编译/下载成功”。修复不会补出真实验证，也不会阻止已经显式授权的 GUI 调用链继续产生原有副作用。

| 能力 | 源码与本次验证支持的判断 | 尚不能证明 |
|---|---|---|
| CLI、dry-run、授权拒绝 | 真实可用，软件回归通过 | 双开关是防误触机制，不是身份认证/MFA |
| 只读 monitor | 硬编码 FC03，选读 FC01/02；软件 TCP 与串口 mock 通过 | 真机寄存器映射、字序、运行状态；通用变量调试/断点 |
| virtual simulator | 三种预置场景、字序、写拒绝及 JSONL 软件闭环通过 | ST 语义、PLC 扫描周期、硬件 I/O、控制算法完整正确性 |
| USB devices | 有解析、只读枚举及降级；attach/detach 有 mock 前后校验 | 唯一身份绑定、进程间所有权互斥 |
| AutoShop build/flash | 有计划、GUI 命令序列、逐步日志及超时 | 实际编译产物正确性、下载与程序身份、真实 RUN/STOP |
| 可发布 Python 包 | editable 源码树场景已有测试 | wheel 独立安装使用 monitor/sim/bridge |

源码入口：[CLI](../../src/plc_idf/cli.py)、[桥式后端](../../src/plc_idf/backends/bridge_autoshop.py)、[虚拟从站](../../wsl-tools/virtual_easy521.py)、[监测客户端](../../wsl-tools/e521_monitor.py)。

## 2. 已落实的有界 P0 修复

### P0-01：禁止把进程退出码当作业务验证成功

修复前，`bridge_autoshop._execute()` 对空命令 `verify` 直接 `continue`；编译产物与下载后复核仅出现在描述中。`inspect` 输出工程标题但未匹配；`log` 输出未判定成功/失败；`diagnostics` 在 PowerShell 中实际返回进程、窗口、会话和 DPI，不返回编译错误列表。

纯内存 fake runner 复现时，让标题为 `AutoShop WRONG_PROJECT`，日志为“编译失败，1 个错误；下载失败”，工程指向不存在的 `Z:\does-not-exist`；修复前 build / flash 都返回 `ok=True`。整个复现没有执行 PowerShell、GUI 或设备通信。

本次修复：空命令的必需校验逐项写入 `bridge.<action>.verification`，最终 `BackendResult.outputs` 包含：

```json
{
  "command_chain_completed": true,
  "verification_status": "unverified",
  "unverified_steps": [{"reason": "required_verification_not_implemented"}]
}
```

最终 `ok=False`，证据收尾事件也为 false，CLI 退出 1 并显示“调用链已完成，结果未核验”。dry-run 标明必需校验尚未实现；标题和 diagnostics 的描述同步纠正。现有调用链在授权后仍可能实际编译或下载；退出 1 表示无法核验结果，不能据此认定设备未改变，也不能盲目重跑。

### P0-02：授权配置必须为布尔 true

修复前 `bool(cfg.bridge.enabled)` 将字符串 `"false"`、`"true"` 和非零整数等视为开启；已复现 `enabled="false"` 配合当次旗标得到授权。本次改为 `cfg.bridge.get("enabled", False) is True`。回归覆盖字符串、整数、列表及对象，只有 TOML 布尔 true 可满足配置条件。

### P0-03：有副作用的 GUI 步骤不自动重放

修复前所有 GUI 步骤沿用 `run_command()` 默认自动重试，超时后可能再次发送 F8、ENTER、打开文件或确认动作。本次只对 `inspect/menus/log/diagnostics` 查询允许有界重试，按键、打开文件、确认及启动等不自动重放。

回归在假 runner 中模拟 F7、F8、ENTER、file 和 accept 超时；即使配置允许 2 次重试，动作均只调用 1 次。另验证只读 inspect 在第一次超时后仍可重试。本次不承诺 Windows 子进程超时后 GUI/下载副作用已经停止。

改动文件：[safety.py](../../src/plc_idf/safety.py)、[bridge_autoshop.py](../../src/plc_idf/backends/bridge_autoshop.py)、[cli.py](../../src/plc_idf/cli.py)。新增回归：[test_bridge_verification.py](../../src/plc_idf/tests/test_bridge_verification.py)；已有 CLI 假执行用例同步改为断言未核验。

## 3. 未修复的优先缺口

| 优先级 | 缺口及直接证据 | 建议完成标准 |
|---|---|---|
| P0 | 设备身份匹配错误：`device.find_plc_entry` 使用 `busid == expected OR vidpid == expected` 并返回首项。已复现相同 VID/PID 更早出现时选错 BUSID，及 BUSID 相同但 VID/PID 不符也被接受 | 明确目标身份、拒绝冲突/歧义；对应 TTY 经 sysfs 关联；多同型号、BUSID 变化及错 VID/PID 的纯软件回归 |
| P0 | 没有设备与 AutoShop 会话的进程间锁。现有 USB 状态检查只是当时快照，不能防另一 CLI 或用户同时切换工程/设备；EvidenceLogger 的线程锁不等于设备租约 | 同设备/同 AutoShop 会话执行串行化；超时/崩溃释放；并发回归；冲突直接失败 |
| P0 | 下载前不绑定已验证构建产物、源代码快照或设备身份；flash 不先 build，固定 F8/ENTER 及等待 10 秒后取日志 | build manifest 含源码/工程/工具版本/产物 SHA-256；flash 只接受明确 manifest；下载对象/当前工程先匹配；设备端结果带来源 |
| P0 | 工程标题、编译日志、产物与下载结果尚无真实语义验证。只改最终未核验状态不能阻止在错误工程上下文继续发送下载按键 | 任一身份/工程/状态不匹配应在有副作用步骤前停止；引入故障注入，坏 ST、错误工程、旧产物与失败下载均不得成功 |
| P1 | wheel 缺外置脚本且无运行依赖声明，详见第 5 节 | 无源码树的干净安装能运行 `sim --with-monitor --once`；声明锁定/兼容的 pymodbus、pyserial 和打包资源 |
| P1 | 配置无完整 schema；节类型、范围、路径及布尔类型大多未校验；显式坏配置只警告后使用默认值；配置的 paths.project_root/wsl_tools 随后被覆盖 | 加载时做类型与边界校验，显式配置错误返回可理解错误；源路径可追踪；测试非布尔、负超时/重试和错误节类型 |
| P1 | 证据是尽力写入且仅截 stdout/stderr 前 200 字，无 trace_id、操作者、源码/产物 hash、完整日志引用和 schema 版本；异常/部分失败收尾不统一 | 全链关联 ID、完整文件引用及结果来源；明确写动作前证据不可写的处理策略，保留原始历史证据 |
| P1 | 缺少源码到 Windows 工程同步；`--project` 只改工程名，目录仍取配置，可能构造错误路径；没有通用 init/check/sync 流程 | manifest 成为工程/目标/源码配置入口；可核验同步结果；编辑 ST 到编译输入之间无人工搬运 |
| P2 | 监测与编排错误边界不完整：sim READY 在 `serve_forever()` 前打印；`wait_ready` 返回后后台读线程持续向无人消费的队列写入；闭环持续监测被配置的默认 60 秒超时限制；TimeoutError/TimeoutExpired 未统一转换成 CLI 诊断 | READY 在真正监听后发出；持续日志排空/限界；长时与单次模式分离；故障退出码与收尾测试 |

其他待补：

- `parse_usbipd_list('1-1 1234:5678 Device Attached\n')` 在列仅单空格时触发 `IndexError`，而非结构化拒绝；解析容错与 docstring 承诺不一致。
- `load_config` 声明的配置发现顺序与实现不完全一致：显式 `--config` 实际优先于环境变量；并非任意嵌套目录都沿途搜索 `.plcidf.toml`，而是先定位脚本标记根目录再查该根目录配置。`Section.to_dict()` 还直接暴露底层可变字典。
- CLI `store_true` 的 false 值会覆盖某些配置 true，例如 `monitor.calibrate=true` 未必按预期生效；应区分“未提供”和“明确 false”。
- GUI `log` 的控件宽度阈值为 1000，截图目录及默认路径包含开发机固定用户目录；`accept` 没有轮询等待，且“编译”子串并不足以判成功；需用真实 AutoShop 版本夹具校准。
- 监测固定读取 D0-D18 的九个 REAL32 量，属于现有 BESS 契约观察器；需要描述驱动的寄存器/变量模型，才能成为通用 PLC-IDF 监测模块。
- 全量 pytest 默认只收集 CLI 测试，现有真实 USB 枚举测试未打单独 hardware 标记。应将纯软件默认集与设备测试显式区分，CI 同时覆盖软件工具集。

以上缺口均未在本次修复中实现。建议先完成 P0 的设备身份、互斥、产物及状态验证，再声称具备可信的无人值守 build/flash 闭环。

## 4. 测试指令、结果与局限

环境：Python 3.12.3；仓库现有 `.venv`，pytest 9.1.1、pymodbus 3.16.1、pyserial 3.5；未改这些依赖，也未连接 PLC 网口或串口。

| 阶段 | 实际指令 | 实际结果 |
|---|---|---|
| 修复前基线 | `.venv/bin/python -m pytest src/plc_idf/tests` | `80 passed in 3.39s`，包含下述真实只读枚举用例 |
| 修复前软件基线 | `.venv/bin/python -m pytest src/plc_idf/tests -k 'not devices_real_readonly'` | `79 passed, 1 deselected in 3.53s` |
| 软件工具基线 | `.venv/bin/python -m pytest wsl-tools/tests` | `65 passed in 16.19s` |
| P0 修复后 CLI | `.venv/bin/python -m pytest src/plc_idf/tests -k 'not devices_real_readonly'` | `95 passed, 1 deselected in 3.10s`；新增 16 个参数化场景 |
| P0 修复后最终软件合并集 | `.venv/bin/python -m pytest src/plc_idf/tests wsl-tools/tests -k 'not devices_real_readonly'` | `160 passed, 1 deselected in 19.37s` |

`git diff --check` 通过。本次测试之后没有再改实现代码。

边界说明：第一次运行现有 CLI 全套时，其中 `test_devices_real_readonly` 检测到 usbipd 可用，调用了只读 `usbipd list` 并探测 TTY 路径。识别到这一点后，后续运行均显式排除此用例。因此第一次整套不能标为“完全不触达 Windows/设备枚举”。本次没有 attach/detach、设备写入、PLC 串口/网口通信或真实 AutoShop GUI 操作。

新桥式回归均在 `_default_runner` 或桥式 `run_command` 边界注入 fake，真实 Windows 可执行程序不会被调用。软件 TCP e2e 只用 `127.0.0.1` 和临时端口；写拒绝用例将 FC05/06 发给本次创建的虚拟软件从站。

测试通过证明当前软件行为满足这些用例，不能证明硬件编译、下载及运行成功。原有“假 GUI 全 rc=0 即成功”的用例恰好说明，绿灯数量必须结合所断言的语义理解。

## 5. 独立打包验证

在 `/tmp/plcidf-package-audit-*` 复制 `pyproject.toml`、`LICENSE`、`src`、readme，以本机已有 setuptools 68.1.2 / wheel 0.42.0 离线调用 `setuptools.build_meta.build_wheel()`，解压到临时 installed 目录，切换到源码树外用该目录为 `PYTHONPATH` 加载配置。没有安装到共享环境、没有联网下载，也没有改仓库打包文件。

实际输出：

```text
wheel_name: plc_idf-0.2.0-py3-none-any.whl
wheel_contains_e521_monitor.py: False
wheel_contains_e521_regs.py: False
wheel_contains_virtual_easy521.py: False
wheel_contains_autoshop_ui.ps1: False
wheel_test_files: 6
monitor_script_exists: False
isolated_import_rc: 0
```

`pyproject.toml` 仅打包 `src/plc_idf*`，未包含外置脚本；`find_project_root()` 的源码树兜底在独立包环境指向临时 site-packages 上层，找不到脚本。`dependencies=[]` 同时未声明 monitor/sim 必需的 pymodbus/pyserial。故“CLI 本体零依赖”不意味着功能安装零依赖。本次未进行修复后的第二轮 wheel 验证，因为 P0 修复没有修改此打包机制，以上为基线可复现结论。

## 6. 建议接续执行任务

1. **执行结果模型与构建验证**：明确 `planned/command_completed/verified/failed/unknown` 的定义；提取工程/源代码/工具版本清单，建立产物与诊断验证器。验收用错误 ST、错误工程、无新产物、旧日志和正确样例。
2. **目标身份与会话租约**：唯一识别设备、关联 USB/TTY，给 Windows GUI 和物理目标各加进程间互斥；验收两进程竞争、多同型号和设备热插拔失败路径。
3. **可交付安装包**：脚本迁入包资源或模块、声明依赖与版本兼容、测试独立 wheel；验收干净环境一条命令完成纯软件演示。
4. **源代码同步与项目 manifest**：让 AI 生成的 ST 可从仓库确定性同步为 AutoShop 的实际输入；编译和下载均引用同一版本化 manifest，禁止静默换工程/旧包。
5. **现场验证任务单**：待软件断言与物理条件明确后，按用户明确的设备操作授权和现场条件验收；保存真实编译/下载/运行各自来源的证据，不能用本报告的软件测试替代。

本报告与新增测试属于本次证据；既有 `wsl-probe/` 和 `plc/easy521/evidence/` 原始记录未改写。
