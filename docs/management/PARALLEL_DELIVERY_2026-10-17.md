# 三路线并行开发：首轮交付

日期：2026-10-17。用户确认三条路线并行，并优先推进路线 2。本轮采用三位 AI 技术执行代理分别实现开放运行时、厂商应用分析、固件/硬件取证工具；统筹完成目标模型、CLI、独立复核、端到端验证、分发和 GitHub 同步。没有聘用外部人员，也没有后台持续执行任务。

## 1. 当前结果

| 路线 | 本轮实际交付 | 尚未完成 |
|---|---|---|
| 1 保留原厂运行时，研究原生兼容 | 标准库应用分析工具；实际 12 文件、6 组差分；ELF 符号/重定位经 readelf 对照 | LiteST 原生前端、完整 runtime ABI、应用封装与原生下载协议 |
| 2 自控目标与开放运行时，优先 | 固定 MatIEC→C→GCC→本地 Linux ST 扫描；CLI 创建/检查/构建/部署/运行/监测/停止；完整正负验证 | 远程控制板、真实 I/O、保持区、完整 Beremiz 集成及工业实时性 |
| 3 底层固件与芯片控制 | 通用有界镜像检查器；ELF/uImage/DTB/平台线索与证据等级；硬件取证矩阵 | Easy521 系统镜像、准确 SoC、板卡修订、启动/BSP/I/O 和恢复验证 |

本轮没有 AutoShop、Windows 进程、USB 或 PLC 操作，也没有刷写固件。这里的 Linux ST 执行目标是本机 WSL/Linux，不是已经接管 Easy521。

## 2. 开放路线的真实执行证据

固定上游 MatIEC 提交与档案哈希，首次引导在独立目录构建；验证后的 `bin/lib/licenses/toolchain.json` 已放入本机默认目录 `/home/zss/.cache/plcidf/matiec`。本机 `doctor --target open-linux --json` 已通过，无需 Windows。没有将上游源码、编译器、厂商库或执行二进制整包加入仓库。

源码必须是包含 PROGRAM/CONFIGURATION/RESOURCE/TASK 的完整 ST。当前明确限制为单 MAIN、一个周期任务及 COUNTER:DINT、HEARTBEAT:BOOL 两个观测字段；这不是通用 ST 全语法或全部硬件能力声明。

[CLI 实测证据](evidence/open-linux-cli-20261017.json) 覆盖：

- 故意语法错误由实际 MatIEC 拒绝，退出 1。
- 正确源码实际生成 C 和 Linux ELF，构建清单绑定输入与产物；未部署 build 不能直接 run。
- flash 默认预览不复制或启动；执行后安装到独立本地 release。
- 真实扫描使 COUNTER 增加并使 HEARTBEAT 翻转，停止动作回读 stopped。
- 将 ST 的加 1 改为加 2 后重新构建、部署，观测值满足 `counter = 2 × scan_count`，且程序哈希改变。
- 正常到期完成；部署字节改动后保留历史样本、降低质量并拒绝再次运行。

[独立 API 实测](evidence/open-linux-api-20261017/summary.json) 另观察到 50 次扫描和加 3 后 13 次扫描得到 39。原始样本来自 MatIEC 生成结构体；C runner 只调度与观测，没有用固定脚本替代 ST 逻辑。

构建前后校验输入；部署后校验复制品；运行仅接受本运行目录内的已部署 release，并用已校验文件描述符启动。stop 核对进程启动时刻、argv 与执行映像，再使用 pidfd；运行有独立内核期限，避免 ST 死循环无限持续。它仍是本地开发运行时，功能通过不代表硬实时或物理 I/O 验收。

## 3. 可复现入口

先激活仓库 `.venv`，或安装独立 wheel。新机器准备 Linux 编译依赖后：

```bash
plcidf toolchain --install --local-autotools --json
plcidf doctor --target open-linux --json
plcidf targets --json
plcidf new /tmp/plc_demo --target open-linux --json
plcidf check /tmp/plc_demo --json
plcidf build --target open-linux --project /tmp/plc_demo \
  --execute --manifest /tmp/plc-build-pointer.json --json
plcidf flash --target open-linux --manifest /tmp/plc-build-pointer.json --execute --json
```

将 flash 返回的 `manifest_path` 交给 `plcidf run --manifest <release.json> --duration 5 --json`；再把 run 返回的 `run_id` 交给 `plcidf monitor --target open-linux --run-id <id> --json` 与 `plcidf stop --run-id <id> --json`。AI 可直接读取 JSON 串联调用。本地路径通过 `--state-dir/--toolchain-dir` 或 `PLCIDF_OPEN_LINUX_STATE/PLCIDF_OPEN_LINUX_TOOLCHAIN` 指定；同一实例需使用同一 state_dir。

一条命令复现完整正负验收：

```bash
.venv/bin/python tools/check_open_linux.py \
  --toolchain-dir /home/zss/.cache/plcidf/matiec --report /tmp/plc-native-report.json
```

这个验收命令会清理临时工程和运行目录，报告保留结果与样本。其他机器将工具链路径换成实际引导目录。

## 4. 另外两条路线的具体入口

```bash
python3 tools/inspect_vendor_application.py path/to/MAIN.o path/to/stprog.o
python3 tools/inspect_vendor_application.py --compare baseline/MAIN.o changed/MAIN.o
python3 tools/inspect_firmware_image.py path/to/firmware.img
```

[应用产物研究](../research/firmware/07-vendor-application-inspection-2026-10-17.md) 的重要事实：已变化的 MAIN/stprog 与逐字未变化的 Output.prg/sdt 同时存在，因此后两者不能单独绑定源逻辑或证明已封装完整应用。没有据未知头字节猜测签名算法。

[固件/硬件取证](../research/firmware/08-hardware-evidence-and-image-inspection-2026-10-17.md) 将格式识别与设备身份分开。本次真实输入是已有 ARM 对象与宿主 ELF 对照，不是新取得的 Easy521 系统镜像；合成 uImage/DTB 仅用于测试，准确芯片与 OS 仍 unknown。

## 5. 验证与剩余任务

- 源码软件回归：**396 passed、13 skipped、1 deselected**；排除真实 USB 枚举，13 项真实工具链用例在默认套件中明确跳过。
- 设置实际工具链后，开放后端套件 **38 passed**，其中包含上述 13 项实际编译/运行用例；与前一项有重叠，不相加冒充独立测试总数。
- 工具与汇报脚本：**54 项通过**，含应用分析 14 项、镜像检查 35 项及进度脚本 5 项。
- 最终 CLI 正负执行与 [sdist→wheel→新 venv→仓库外分发](evidence/distribution-open-linux-20261017.json) 通过；模板与引导脚本随包分发，缺失工具链得到结构化 unavailable，不依赖 Windows。
- CI 新增独立 open-linux job，在干净 Ubuntu 上引导固定编译器后运行真实后端与 CLI 验收；源码/mock/虚拟网络测试与它分别报告。

T11/#15 已交付待合并。T10/#14 已交付两个目标与独立请求/工程模型，但完整后端注册迁移、设备身份 v2 与所有产物契约统一仍开放。T12/#16 的跨目标执行对照尚未通过；Easy521 实际运行条件与厂商协议仍需补齐。R01/#10、R02/#11 已有可复用工具与新证据，不按“有工具”关闭完整研究任务。
