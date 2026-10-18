# 开放 Linux ST 执行后端

2026-10-17。这个后端在 WSL/Linux 中真实编译并执行完整 ST 工程，包含 `PROGRAM / CONFIGURATION / RESOURCE / TASK`。它使用固定 MatIEC 的 `iec2c` 生成 C，再用 GCC 编译为 Linux ELF；本仓库的 C runner 调度生成的 `config_run__()` 并读取生成实例的字段。运行器没有重写计数器或心跳逻辑，修改 ST 的每扫描增量会直接改变观测结果。

本轮实现的是**本地开发运行时**，没有调用 Windows、AutoShop 或物理 PLC。尚未接入 Beremiz 完整运行时、远程 RPC、真实 I/O 驱动、保持存储与工业实时调度；这个后端通过不能算作 Easy521 已接受开放固件。

本轮选用 MatIEC 加受限的本地扫描运行器，先把真实 ST 代码生成、部署与执行从 GUI 依赖中拆出；Beremiz 的完整服务、RPC 和更广任务能力留作后续适配。这一选择改变的是首个后端的实现范围，不改变逐目标验收要求。

## 可复现依赖

基础示例源码：[counter/main.st](../../examples/open_linux/counter/main.st)。上游固定到 [`beremiz/matiec@3a41303bcb4e50403417bc39c63f88981d7d961e`](https://github.com/beremiz/matiec/tree/3a41303bcb4e50403417bc39c63f88981d7d961e)。下载档案 SHA-256 为 `21d249d9c65ff1a26404cfe0969a14a0c12d05e53871e26d77bdd4e8fd0a89a2`，不追踪浮动分支。

需要 Linux、Python 3.12+、GCC/G++、make、bison、flex、m4、Perl，以及 autoconf/automake。启动脚本不运行 sudo、不向系统目录安装内容：

```bash
python3 tools/bootstrap_open_linux.py --directory /tmp/plcidf-open-linux-toolchain --jobs 4
```

Ubuntu/Debian 未安装 autotools 时，可以增加 `--local-autotools`。它用 `apt-get download` 下载三个构建包，再用 `dpkg-deb -x` 解压到指定目录；实际包名与 SHA-256 进入 `toolchain.json`，不会安装 dpkg 软件包。其他构建依赖缺失时明确失败。网络读取和外部命令都有超时，make 上限 300 秒；超时时终止整个构建进程组。

`toolchain.json` 记录编译器 SHA-256、库文件 SHA-256、依赖版本和许可证。上游编译器采用 GPL-3.0-or-later；C 支持头文件存在 LGPL-2.0-or-later 与 LGPL-3.0-or-later 的各自声明，必须保留具体文件通知。源码、头文件、编译器和运行二进制均位于指定的外部目录，没有整包加入本仓库或默认 Python wheel。分发生成的运行二进制前仍需按具体所用组件处理许可证义务。

## 独立 API

同日后续扩展已加入通用标量字段、确定扫描 replay，以及 `verify` 一键场景验收。完整入口见 [AI 编程说明](../AI_PROGRAMMING.md) 和 [本批交付](../management/AI_WORKFLOW_DELIVERY_2026-10-17.md)。以下 counter API 保留兼容；它现在是基础例子，另外已有 [motor](../../examples/open_linux/motor/README.md) 与 [analog_hysteresis](../../examples/open_linux/analog_hysteresis/README.md)。

统一 CLI、默认安装目录、实测指令与三路线状态见 [并行交付报告](../management/PARALLEL_DELIVERY_2026-10-17.md)。`new/build/flash/run/monitor/stop --target open-linux` 已接入；运行控制的目标默认为 open-linux，旧 Easy521 默认路径保留。

`src/plc_idf/backends/open_linux.py` 不依赖现有 Windows 请求对象。构造不创建目录；`install()` 是已安装工具链的**只读完整性检查**，下载与编译由上述 bootstrap 单独完成。

```python
from plc_idf.backends.open_linux import OpenLinuxBackend

backend = OpenLinuxBackend(
    toolchain_dir="/tmp/plcidf-open-linux-toolchain",
    state_dir="/tmp/plcidf-open-linux-state",
)
assert backend.install()["ok"]

build = backend.build("examples/open_linux/counter/main.st",
                      "/tmp/plcidf-open-linux-builds", cycle_ms=20)
assert build["ok"]
preview = backend.deploy(build["manifest_path"])  # 不复制或启动程序
release = backend.deploy(build["manifest_path"], execute=True)
run = backend.run(release["manifest_path"], duration_s=5,
                  sample_interval_ms=100)
observation = backend.monitor(run["run_id"])
stop_result = backend.stop(run["run_id"])
```

接口均返回带 `ok/status` 的 JSON 可序列化字典；无效输入抛出 `OpenLinuxError(ValueError)`，文件系统错误保留 `OSError`。编译错误以 `ok=false/status=failed/stage=iec2c` 或 `stage=gcc` 返回真实退出码与诊断。

兼容 profile 为 `open-linux-counter-v1`：单个 `PROGRAM MAIN`，观察字段 `COUNTER:DINT` 与 `HEARTBEAT:BOOL`；配置为 `Config0/Res0/scan/instance0`，周期 `T#Nms`，优先级 0。周期必须与 `cycle_ms` 一致，且生成 C 的 ticktime 还会再次核对。IEC 标识符不区分大小写。外部源引用、inline C、pragma、多个任务/资源和物理 I/O 不在这个 profile 的验收范围；不支持的结构拒绝执行。

新增 `open-linux-signals-v1` 由 `build(..., signals={"inputs": {...}, "outputs": {...}})` 显式选择。支持 BOOL、INT、DINT、UINT、UDINT、REAL，最多 64 个直接 MAIN 字段，类型与 MatIEC 生成结构再次核对；任务结构限制相同。共享校验器拒绝重复或重叠名称、不支持类型及越界输入。观察值不再要求计数器或心跳。

`replay(release_manifest, frames, timeout_s=5)` 每帧执行一次生成的扫描，第一扫描 IEC 时间为 0，此后增加声明周期；省略的输入保留前次输入映像，初值来自 ST。每次 replay 独立初始化，最多 10000 帧、最长 30 秒，外部进程期限会杀掉不返回的扫描并回收进程。样本保留 run/program/source/binary/build 身份、逐扫描虚拟时间与 `values`。这些是逻辑时间，不能当成 Linux 实际调度性能。

这只是首个明确受限的可运行 profile。不要为了显示“支持多 PLC”而忽略类型、任务或 I/O 声明；扩展方式见 [ADR-0002](ADR-0002-native-multitarget.md)。

## 产物、部署与运行证据

每次 build 创建新目录，保留本次源码、生成 C、runner、编译器诊断和 ELF。GCC 前后核对构建输入，工具链再次核验，写入 `build.json` 后立即读回全部产物校验，再返回 verified。清单绑定源码、工具链、运行器、目标架构与二进制哈希。

`deploy()` 将核对过的 bundle 复制到 `state_dir/releases/<build_hash>`，复制后再次核对。`run()` 只接受本 state_dir 内已部署的 `release.json`；不能直接运行任意 ELF 或跳过部署。启动时核对二进制，使用同一个已校验 inode 的文件描述符执行。

新构建样本 schema 2 包含 `run_id / program_hash / source_sha256 / binary_sha256 / build_hash / scan_count / values / elapsed_ns / cycle_ns`；兼容 counter profile 另外保留原 counter/heartbeat 字段。原 schema 1 release 保留运行路径，但须重新构建才能 replay。`monitor()` 同时检查进程启动时间、argv、实际运行映像和当前 bundle；若文件改变，保留历史样本并降低质量，不继续报告当前身份 verified。运行中长时间没有样本会标记 stale，连接或进程存在不能代替扫描进度。

默认运行 5 秒，上限 3600 秒。正常到期返回 completed；主动停止回读 stopped；无正常终态的进程退出返回 unverified/faulted。C runner 采用 monotonic absolute sleep 调度，另设内核 monotonic deadline（请求时长加 1 秒）终止不返回的 ST 扫描；这只是本机开发的运行边界，不构成工业看门狗或实时性认证。`stop()` 校验 `/proc` 启动时间、argv 和执行映像，再通过 pidfd 发送停止信号，防止 PID 复用误杀其他进程。

本运行器是本机开发工具，不是针对恶意同用户进程的安全沙箱。首次验收例子无物理 I/O；迁移至板级控制前，必须补齐 I/O 驱动、调度、故障输出、保持与恢复的独立验收。

## 本次实际结果

[原始证据汇总](../management/evidence/open-linux-api-20261017/summary.json)与同目录的构建、部署、监测记录：

- 完整 ST 经真实 MatIEC/GCC 编译，实际完成 50 次扫描；counter 从 1 增至 50，heartbeat 同时观察到 false 和 true。
- 将 ST 的 `COUNTER + 1` 改为 `COUNTER + 3` 后重新构建，13 次扫描观测到 counter=39，程序哈希改变。这直接检验了应用行为来自 ST。
- 故意语法错误 `COUNTER := ;` 在 MatIEC 阶段以退出码 1 失败，没有生成可部署成功清单。
- 正常完成、主动停止、构建中源码变化、部署后改包、PID 复用、ST 死循环内核期限，以及异常退出保留旧样本的回归均有覆盖。

真实工具链回归命令：

```bash
PLCIDF_OPEN_LINUX_TOOLCHAIN=/tmp/plcidf-open-linux-toolchain \
  .venv/bin/python -m pytest src/plc_idf/tests/test_open_linux.py -o addopts= -q
```

本次 38 项通过；未设置工具链环境变量时，只运行离线契约用例并明确跳过真实编译用例，不把跳过视作工具链已通过。
