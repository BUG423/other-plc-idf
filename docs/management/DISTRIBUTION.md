# 独立安装与分发验收

PLC-IDF 的普通安装现在包含运行依赖和四个运行资源，可在没有源码树的目录中运行 `monitor/sim`。Windows 桥接脚本也随包分发；AutoShop、Windows 交互桌面和设备驱动仍由目标电脑提供。

## 安装和运行

使用 Python 3.12 或以上版本，在任意工作目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install /path/to/plc_idf-0.2.0-py3-none-any.whl
.venv/bin/plcidf sim --with-monitor --once
```

安装会自动取得经过验证的 `pymodbus==3.16.1` 与 `pyserial==3.5`。如需完全离线安装，先在联网环境下载 wheel 及依赖：

```bash
python -m pip download --dest wheelhouse /path/to/plc_idf-0.2.0-py3-none-any.whl
.venv/bin/python -m pip install --no-index --find-links wheelhouse plc-idf==0.2.0
```

以上不代表包已发布至 PyPI；使用本项目实际构建的 wheel。

## 离线工程入口与诊断

```bash
plcidf new my_plc --cycle-ms 5 --json
plcidf check my_plc --json
plcidf doctor --json
```

已有空目录可使用 `plcidf init [PATH] --name NAME`，省略路径时使用当前目录。`new/init/check/doctor --json` 的 stdout 为一个 `schema_version: 1` JSON 文档；创建拒绝覆盖非空目录。检查失败返回 2，文件/进程异常返回 1，监测或模拟等待超时返回 124。

`check` 返回文件、行、列和诊断代码。它检查有限的内存变量 ST 范围，并非完整编译器。`doctor` 只读取本地版本和文件状态，报告包的安装方式、脚本资源、Python 依赖及 Windows 软件文件条件；不启动 Windows 程序，不枚举 USB，不访问网络或 PLC。文件存在不证明版本兼容、连接成功或编译/下载已经通过。默认诊断的成功条件是本地监测/模拟运行条件齐全，Windows 条件单独报告。

## 资源和配置规则

- 项目根优先采用显式参数、`PLCIDF_ROOT`（兼容 `PY2PLC_ROOT`），然后从当前目录向上寻找 `.plcidf.toml` 或源码树标记。源码 editable 安装可以回到源码根；普通安装回到当前目录。默认日志位于该工作目录的 `logs/evidence.jsonl`，不会写入 `site-packages`。
- `.plcidf.toml` 的配置发现保留环境变量与显式 `--config`。`paths.wsl_tools` 可覆盖脚本目录，相对路径基于项目根。
- 默认脚本先找项目中的原始文件，再找 editable 安装的源码文件，最后找包内 `_resources`。桥接脚本转换为当前 WSL 发行版的 Windows UNC 路径，也可通过 `bridge.tools_ps1_windows` 明确指定。
- `wsl-tools/e521_regs.py` 继续作为寄存器契约唯一事实源。`setup.py` 在构建时复制三个 Python 运行文件和 `plc/tools/autoshop_ui.ps1`；仓库中不维护第二份源码，验收逐字节比较原文件与 wheel 文件。

## 可重复验收

在源码根目录执行：

```bash
.venv/bin/python -m pip install -r requirements-ci.txt
.venv/bin/python tools/check_distribution.py \
  --dist-dir dist \
  --report docs/management/evidence/distribution-20261017.json
```

验收流程先构建 sdist，再从该 sdist 构建 wheel；创建未启用系统 site-packages 的新 venv；在仓库外安装 wheel 和依赖；确认实际导入来自新 venv；运行 `steady/low_voltage/overload` 三个软件场景、监测脚本帮助、`build` dry-run 和旧命令别名。脚本清除源码导入路径及项目环境变量，检查日志留在临时用户工作区，并记录版本、资源哈希及产物哈希。

证据见 [distribution-20261017.json](evidence/distribution-20261017.json)。临时安装目录在结束时删除；指定 `--dist-dir dist` 时保留安装包。软件场景只验证监测链，不执行用户 ST，不证明真机程序正确；本验收未访问 USB、Windows GUI 或 PLC。

CI `software-tests.yml` 已加入相同流程。是否已在 GitHub 执行通过，以相应 workflow run 为准。
