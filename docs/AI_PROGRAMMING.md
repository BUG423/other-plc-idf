# 在 WSL 中让 AI 编写并验收 ST

这个工作流在电脑上真实编译和执行 ST。你不需要再准备一台 PLC 来验证软件控制逻辑。现有 Easy521 的下载与设备运行有独立的验收流程；这里生成的 Linux ELF 不是 Easy521 应用包。

在项目根目录进入当前 Python 环境：

```bash
source .venv/bin/activate
plcidf doctor --target open-linux --json
```

工具链缺失时执行 `plcidf toolchain --install --local-autotools --json`。它在用户缓存中构建固定的 MatIEC；需要系统已有 C/C++ 编译等依赖。已安装时无需重复安装。

## 创建一个可以自动验收的控制程序

```bash
plcidf new /tmp/ai_motor --target open-linux --template motor --json
plcidf check /tmp/ai_motor --json
plcidf verify /tmp/ai_motor --json
```

工程包含 `main.st`、`plcidf.json`、`scenarios.json`、说明和给 AI 阅读的 `AGENTS.md`。电机例子只是逻辑教学模型，输出保存在内存；它覆盖启动边沿、运行保持、停止优先、故障/许可联锁与 60ms 启动延时。验收每个场景时都重新初始化实际生成的控制程序。

给当前终端 AI 的指令可以直接写成：

> 阅读 /tmp/ai_motor 中的工程和 AGENTS.md。解释当前启停逻辑，将起动延时从 60ms 改成 100ms，并依据新需求更新延时边界场景；保留停止优先和故障后需重新启动的要求。运行 plcidf verify --json，逐项解释失败再修正。最后报告源哈希、场景结果和未验证的硬件能力。

AI 在文件中改代码、调用命令并读取 JSON，不需要复制到 AutoShop。场景失败时报告会关联具体场景、步骤、扫描、信号的 expected/actual；语法错误则保留真实编译器诊断。退出码 0 表示本次必需验证通过，非零需读取失败阶段。机器可读模式只向 stdout 输出一个 JSON 文档。

## 描述自己的输入、输出和需求

在 manifest 中声明受支持标量类型，然后在 MAIN 中声明对应字段。例：

```json
"signals": {
  "inputs": {"START": "BOOL", "STOP": "BOOL", "PERMISSIVE": "BOOL"},
  "outputs": {"MOTOR": "BOOL"}
}
```

支持 `BOOL / INT / DINT / UINT / UDINT / REAL`；当前保持 `MAIN/Config0/Res0/scan/instance0` 的单任务结构。`observables` 与输出类型表一致。物理地址绑定、多个任务、任意复合类型、外部代码不在本 profile 中。

场景文件示意（完整运行例子见生成工程）：

```json
{
  "schema": "plcidf.scenarios.v1",
  "scenarios": [{
    "name": "stop_has_priority",
    "steps": [{
      "scans": 1,
      "inputs": {"START": true, "STOP": true, "PERMISSIVE": true},
      "expect": {"MOTOR": false}
    }]
  }]
}
```

每步先更新输入，再执行指定次数的扫描，最后比较 expect；没有更新的输入继续保留。第一扫描 IEC 时间为 0，此后增加工程周期。每一步至少有一个输出断言。REAL 浮点输出可用 `{"approx": 1.5, "abs_tol": 0.001}`；期望用实际工程单位表达。

把“必须发生”和“不得发生”都写进场景：启动前、延时未到、延时边界、正常保持、启动与停止同时出现、失去许可、故障后恢复与重新启动。需求改变时才调整期望，避免用修改测试掩盖代码错误。

## 查看报告与本地进程

默认验收报告为工程内 `.plcidf/verification.json`。报告包含源文件/场景身份、编译与部署结果、逐步输出、断言与原始样本路径。可用 `--report /绝对路径/report.json` 单独留存。编译器目录、运行时目录与报告位置均可显式指定。

需要观察实际墙钟运行与停止时，沿用 `build --target open-linux --project PATH --execute`、`flash --target open-linux --manifest BUILD_MANIFEST --execute`、`run --manifest RELEASE_MANIFEST`、`monitor --target open-linux --run-id ID`、`stop --run-id ID`。这些路径和 ID 从上一条命令的 JSON 获取。实时模式目前没有交互式现场输入驱动，动态输入序列由 `verify` 场景提供。

`verify` 是软件需求验收入口。真机时序、24V I/O、电机、接触器、现场安全逻辑、断电与恢复仍需对应目标上的实测，不能从软件场景结果推出。
