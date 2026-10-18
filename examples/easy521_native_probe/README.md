# Easy521 原生工程的内存探针

本目录提供两份 **MAIN 正文**，供当前已存在的 `BESS_E521_DEV` 原生工程使用。它们没有 `PROGRAM / VAR / CONFIGURATION / TASK` 包装，不新增变量、不配置任务周期、不调用 `fbBESS`，不包含物理 X/Y、运动或通信写指令。只能经原生工程同步与厂商编译器验证；不能将 Open Linux 的完整 ST 配置直接粘贴进厂商 MAIN。

两份源码尚未因本目录的离线检查而成为“编译通过”或“真机通过”。Windows、下载和真机操作由统筹执行；本目录生成与单测没有访问设备。

| 文件 | 版本标志 `g_rBESS_SensVP` | 步长 `g_rBESS_SensVQ` | 每扫描 Confidence 增量 |
|---|---:|---:|---:|
| [MAIN_plus1_body.st](MAIN_plus1_body.st) | 52101.0 | 1.0 | +1.0 |
| [MAIN_plus2_body.st](MAIN_plus2_body.st) | 52102.0 | 2.0 | +2.0 |

## 变量确实存在，但没有已验证的 D 区映射

已读取现有 [MAIN 变量清单](../../plc/easy521/MAIN_5ms_variables.csv)、[原生导入清单](../../plc/easy521/autoshop_global_import.csv)、归档 DEV 的 UTF-16 `VarNameAndAddr.csv`，并检查原有 FB 正文。所用变量都已存在；导入表中软元件地址为空、网络属性为私有，编译符号表将其定位于 `0x210...` 内部变量区。原 FB 正文是内存运算，未找到物理 I/O、运动或外发通信调用；本探针完全不调用该 FB。

这些是仓库中已有工程的证据，不能代替本次在线工程绑定审查，也不能证明整个控制器不存在其他程序或映射。原生符号表中的地址是 AutoShop 符号索引，**不能作为 CPU 内存地址或 Modbus 寄存器地址使用**。

`modbus_register_map_real32.csv` 描述应用协议设计，未证明这些 `g_*` 全局变量已经绑定到 D0、D14 等寄存器。因此本探针优先通过 **USB 上的 AutoShop 在线变量表**读回；不猜 D 地址，不据此向 Modbus 写值。`M8000/M8001/M8002` 则确实出现在既有原生符号表中：运行、停止、首运行扫描标志。本源码只读取 M8002。

## 本探针中的临时含义

| 既有变量 | 探针含义 |
|---|---|
| `g_iBESS_Mode` | 每扫描加 1，范围 1..9999，下一扫描重新开始 |
| `g_rBESS_Confidence` | 当前轮累计值；版本 1 为 scan，版本 2 为 2×scan |
| `g_rBESS_SensVP / SensVQ` | 固定版本标志与步长 |
| `g_rBESS_InfluenceP` | 每 100 扫描发布一次的扫描位置，0、100、200……9900 |
| `g_rBESS_InfluenceQ` | 同时锁存累计值，必须等于步长×InfluenceP |
| `g_xBESS_ControllerReady` | 每 100 扫描翻转，等于 `(InfluenceP / 100) MOD 2` |
| `g_xBESS_Enable / MeasurementsValid` | 每扫描设为 FALSE |
| `g_xBESS_Reset` | 每扫描设为 TRUE |
| `g_rBESS_Pcmd / Qcmd` | 每扫描设为 0.0，仅表示两个内存量为零 |

这些名称在探针期间不再表示储能算法的模式、置信度或就绪条件。0 内存指令不能证明外部设备收到零指令。源文件只证明本 MAIN 未直接操作物理 I/O；现场输出隔离与其他程序的状态由本次实机流程另行确认。

M8002 首运行扫描、版本改变或计数越界会复位探针状态。每个完整计数轮中最大累计值为 19998.0，可被 REAL32 精确表示，不会触及 INT16 上界。

## 验收顺序及预期

1. 记录本次设备型号/版本、在线目标、原生工程完整路径、变量绑定与原源码；保留可用的设备上载备份和恢复材料。
2. 在工作副本同步 `MAIN_plus1_body.st`，通过真实厂商编译，记录源/产物哈希。编译通过本身不能证明设备更新。
3. 通过厂商应用下载路径部署；以厂商程序一致性比较或等价的明确读回证据确认设备应用。不要把普通 Modbus 读取或 GUI 按键返回当作下载完成。
4. 发出 RUN 后，读取实际状态；如可见，`M8000=ON / M8001=OFF`。记录至少 3 次新鲜在线变量样本，看到 Mode 与 InfluenceP 改变、Ready 两种状态，并验证标志 52101、步长 1、锁存 Q=P。
5. 发出 STOP，确认实际停止状态；如可见，`M8000=OFF / M8001=ON`。连续至少 3 次读回中的探针内存应保持不变。软件请求成功但读回仍 RUN/STOP 未知时，不算状态切换验证通过。
6. 使用 `MAIN_plus2_body.st` 再次编译、部署、比较、RUN。读回标志必须变为 52102、步长 2，并观察锁存 Q=2×P。这能排除只读取旧 +1 应用或固定播放值的情况；仍需与设备身份、原生下载一致性和构建记录一起形成证据链。
7. STOP 并按本次约定的恢复方案结束；不能将这个临时探针当作储能控制程序投运。

原工程历史配置为 5 ms，探针不改变它。每 100 扫描发布在这一配置下名义上约为 0.5 秒；这不是实测周期承诺。建议以不同时间间隔取样，直到同时观察两种心跳状态，避免显示刷新与采样混叠。

GUI 表格可能分批刷新，Mode 与 Confidence 两个实时字段未必属于同一扫描。因此优先验证较慢锁存的 InfluenceP/Q 和 Ready；若碰到锁存更新瞬间不一致，重新取样并保留原始证据，不放宽为“任意数值都通过”。上述检查不测量 5 ms 周期，也不证明硬实时性。

## 本地检查器

`plc_idf.easy521_probe.inspect_probe(path, revision)` 仅检查已审阅源码、既有变量/导入/符号证据，不调用 AutoShop。两份源码的检查结果与证据哈希见 [source-review.json](source-review.json)。

`assess_probe_samples(samples, revision, expected_state="RUN" / "STOP")` 检查采集后的在线读数。每项结构如下，`values` 需包括上表全部 12 个 `g_*` 字段；可额外包含只读 `M8000/M8001`。布尔值支持 JSON true/false 及厂商 ON/OFF 文本。

```json
{
  "online": true,
  "plc_state": "RUN",
  "values": {
    "g_xBESS_Enable": false,
    "g_xBESS_Reset": true,
    "g_xBESS_MeasurementsValid": false,
    "g_rBESS_Pcmd": 0.0,
    "g_rBESS_Qcmd": 0.0,
    "g_iBESS_Mode": 130,
    "g_rBESS_Confidence": 130.0,
    "g_rBESS_SensVP": 52101.0,
    "g_rBESS_SensVQ": 1.0,
    "g_rBESS_InfluenceP": 100.0,
    "g_rBESS_InfluenceQ": 100.0,
    "g_xBESS_ControllerReady": true
  }
}
```

上例为格式示意，不是真机样本。`online` 和 `plc_state` 必须来自本次采集证据；函数不能自行认证这些声明，所以结果明确保留设备身份、下载一致性和周期测量的独立验收项。

```bash
.venv/bin/python -m pytest src/plc_idf/tests/test_easy521_probe.py -o addopts= -q
```

本地 23 项测试通过，测试使用合成输入验证检查器的拒绝逻辑，**不作为硬件运行成绩**。
