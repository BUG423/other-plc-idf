# AI ST 编程与场景验收交付

2026-10-17，关联 [T13/#17](https://github.com/BUG423/other-plc-idf/issues/17)、[PR #13](https://github.com/BUG423/other-plc-idf/pull/13)。本批次承接用户“只有已连接 Easy521，不知道如何测试，希望直接 AI 编程”的要求，优先完善电脑端真实执行与自动验收。没有新增 Windows、USB、PLC 下载、RUN/STOP 或固件写操作。

## 可以直接使用的结果

`plcidf new PATH --target open-linux --template motor` 生成可编辑 ST、版本化工程、逐扫描需求场景、README 和工程内 AI 操作说明。`plcidf verify PATH --json` 自动检查、实际编译、部署到本地运行目录、逐场景执行生成 ELF 并断言；每个场景重新初始化。用户不需要另备硬件或手动注入测试信号。

通用输入/输出支持 BOOL、INT、DINT、UINT、UDINT、REAL，最多 64 个直接 MAIN 标量字段。实际生成的 MAIN 结构与 manifest 类型必须一致；不依赖全源匹配猜测字段类型。旧计数器工程和 release 保留兼容路径。实时 `build/flash/run/monitor/stop` 同样支持通用字段，场景输入由同步 `replay` 驱动。

执行逻辑来自固定 MatIEC 生成的 C 和 GCC ELF。确定场景第一扫描 IEC 时间为 0，以声明的周期递增；不是用 Python 重写控制逻辑。报告关联源码、工程、场景、编译程序、二进制、部署与逐扫描样本；错误 ST、断言不符、输入变化、坏证据或超时导致失败。

## 实际业务逻辑验证

- `motor`：6 个场景、56 个输出断言，覆盖 60ms 延时边界、启停保持、停止优先、故障/失许可清保持、启动边沿与禁止持高自动重启。
- `analog_hysteresis`：3 个场景、16 个断言，覆盖 REAL 输入、80/70 阈值滞回、禁用复位及 ECHO 浮点输出容差。
- 把 PT 从 60ms 改成 100ms 后，旧验收失败；按明确的新要求调整边界后，实际验证 t=80ms 仍停、t=100ms 才起动。两次实际程序身份不同。
- 故意错误 ST 在 MatIEC 阶段失败，诊断由 `verify` 返回。未知输入场景在构建/部署前拒绝。恢复实现后原验收重新通过。
- 同一个通用工程完成本地进程运行、监测与停止；另以 7/60/1000ms 任务周期真实执行六场景，验证定时边界生成。

原始 CLI 结果与逐扫描数据保存在 [ai-workflow-20261017.json](evidence/ai-workflow-20261017.json)。报告中的临时工程路径已经清理，原始样本嵌入报告便于复核，不把消失的临时路径当成可用附件。

## 复现

```bash
source .venv/bin/activate
plcidf doctor --target open-linux --json
plcidf new /tmp/ai_motor --target open-linux --template motor --json
plcidf verify /tmp/ai_motor --json
```

默认报告位于工程 `.plcidf/verification.json`。AI 可读取失败场景/步骤/扫描及 expected/actual，修正 ST 后重新验收。给 AI 的完整示例指令与文件格式见 [使用说明](../AI_PROGRAMMING.md)；设计决定见 [ADR-0003](../architecture/ADR-0003-ai-verification.md)。

真实端到端验收：

```bash
.venv/bin/python tools/check_ai_workflow.py \
  --toolchain-dir ~/.cache/plcidf/matiec \
  --report /tmp/plcidf-ai-workflow.json
```

独立 wheel 验证增加可选工具链参数，在仓库外新 venv 执行新建 motor 与真实 verify。GitHub Linux CI 同时跑离线软件、实际工具链后端及 AI 工作流。

## 本地最终验证记录

- 默认源码与监测套件：535 passed、51 skipped、1 deselected。51 项实际编译/执行测试需要工具链环境变量，单独启用；真实 USB 枚举排除。
- 显式启用工具链后的三个后端/验收文件：192 passed，与上述离线套件有重叠，不相加。包含类型边界、直接字段绑定、死循环期限、中断清理、坏样本与输入/证据变化。
- CLI AI 工作流：9 类正负与规则变更检查通过，包含 motor 6 场景/56 断言、analog 3 场景/16 断言、100ms 新规则及 7/60/1000ms 周期。
- sdist→wheel→全新 venv→仓库外：旧三种模拟监测、资源校验、缺失工具链诊断与新 motor 真实编译/6 场景/56 断言均通过；见 [分发证据](evidence/distribution-ai-workflow-20261017.json)。
- 本地可继续编辑工程已生成于 `.plcidf/workspaces/ai_motor`，6 场景/56 断言通过，报告位于其 `.plcidf/verification.json`。该目录是忽略的本地工作区，不随 Git 上传；受版本控制的模板与复现命令已入库。
- 原 counter CLI 五类正负检查及工具/汇报 54 项回归通过。源码文件哈希、命令/计数与零残留本地运行进程记录见 [验证汇总](evidence/ai-workflow-validation-20261017.json)；[旧 CLI 回归](evidence/open-linux-cli-ai-regression-20261017.json) 单独保留。

## 验证范围与后续

这次交付完成本地软件逻辑开发和自动验收，尚不覆盖实时运行的交互输入、物理 I/O、工业实时调度、保持存储和远程板卡部署。有限场景通过也不表示任意控制要求已被证明。

Easy521 真实应用下载、设备端程序一致性及运行验收仍为 T09；它是否能运行开放固件、准确 SoC/启动链/BSP 仍有独立未知条件。该 Linux 程序包不能直接烧进 Easy521。跨目标验收 T12 只具备 Linux 场景基础，没有设备端对照结果。

GitHub 汇报工作流仍需合并默认分支后才能定期触发；其作用是整理任务元数据，不会后台执行研发或 PLC 操作。
