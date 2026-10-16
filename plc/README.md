# 储能自适应控制器 PLC 移植

> **Easy521-0808TN 用户注意：** 实际 AutoShop 工程请使用 [`easy521/`](easy521/) 目录中的 LiteST/REAL32 专用版。本目录下的 `LREAL/USINT` 通用版仅作双精度算法参考和差分验证，不能直接粘贴到 Easy521 LiteST。

2026-09-28 已完成 Easy521 原生 AutoShop 工程、零错误编译和两轮 28/28 厂商模拟器验收。用户授权并确认隔离后，正式 DEV 已通过 USB 下载且程序校验一致，原工程备份仍保留；三次 RUN 请求均失败，设备仍 STOP，硬件用例和周期验收未完成。独立硬件 BENCH 已编译，尚未下载。最新方法和限制见 [实际厂商验收报告](easy521/HARDWARE_COMMISSIONING_REPORT.md)。

本目录给出 `modbus_realtime.py` 中 `control()` 的 IEC 61131-3 Structured Text 实现。控制公式、门限、在线辨识、低压/过载状态机及原 20 维延迟状态均与 Python 和 SLX 中的 `Supervisor Step` 对齐。

## 文件

- `FB_BESS_AdaptiveSupervisor.st`：核心功能块，PLC 每次调用完成一个控制周期。
- `GVL_BESS_IO.st`：与现场 I/O 或通信层连接的逻辑变量。
- `PRG_BESS_Control.st`：功能块调用示例，放入 5 ms 周期任务。
- `modbus_register_map.csv`：保持与现有 Python 程序一致的 Modbus 寄存器协议。
- `verify_reference.py`：不启动 GUI/Modbus 的离线验收脚本，检查 PLC 常量及关键状态序列。
- `tests/run_full_validation.py`：边界、状态、100,000 周期差分和闭环 BESS 仿真。
- `VALIDATION_REPORT.md`：需求追溯、实测结果、发现项和最终投运检查表。

## PLC 工程接入

1. 在支持 IEC 61131-3 ST 的工程中新建同名 GVL、FB 和 Program，并依次导入/粘贴三个 `.st` 文件。不同厂商对完整文本导入格式略有差异，但算法正文只使用 `LREAL`、`BOOL`、`USINT`、`ABS`、`MIN`、`MAX` 和 `SQRT` 等标准元素。
2. 新建周期任务，周期设为 **5 ms**，把 `PRG_BESS_Control` 挂到该任务。不要把 20 ms 的 Python `root.after(20, ...)` 当作模型周期；SLX 的控制采样时间是 0.005 s。
3. 将 7 个测量变量一次性更新为同一通信帧/同一采样点的数据，再置 `xBESS_MeasurementsValid=TRUE`。半帧新数据与半帧旧数据不能混用。
4. 首次启动或重新建立测量链路后，至少一个扫描周期置 `xBESS_Reset=TRUE`，随后清零；确认数据有效后再置 `xBESS_Enable=TRUE`。
5. 将 `lrBESS_Pcmd`、`lrBESS_Qcmd` 接到变流器指令或通信发送区。通信丢失时应立刻清除 `xBESS_MeasurementsValid`；功能块会输出零指令并冻结辨识状态。

## 信号与符号约定

- `Vpu`：BUS18 相电压 RMS 标幺值，模型换算为 `Vrms * sqrt(3) / 12660`。
- `Pline`、`Qline`：线路功率标幺值，模型基准为 2 MVA；正负方向必须与模型保持一致。
- `loading`：线路电流负载率，模型中为 `Irms / 110 A`。
- `SOC`：0～1。
- `Pess > 0`：储能放电；`Pess < 0`：储能充电。`Qess > 0` 为控制器用于低电压支撑的方向。
- `Pcmd`、`Qcmd`：标幺指令。模型限值为 `|Pcmd| <= 0.8`、`0 <= Qcmd <= 0.4`（低压模式下）。
- 模式：0 正常，1 低压，2 正向过载，3 反向过载。

## Modbus 对接

现有 Python 是 Modbus TCP 客户端：一次读取保持寄存器 0～27，按 7 个 big-endian `binary64` 解码；再把两个 `binary64` 指令写入 28～35。PLC 替代 Python 时，Modbus 客户端功能块、连接句柄、完成/错误握手以及字节重排必须按 PLC 厂商库实现，核心算法不应直接等待网络事务。

推荐把通信与控制拆成两个任务：通信任务完成整帧读取后发布一个原子测量快照；5 ms 控制任务始终读取最近一帧有效快照。若通信周期无法稳定达到 5 ms，应保留 5 ms 控制任务并保持最近测量，同时使用通信超时监视；超时后令 `xBESS_MeasurementsValid=FALSE`。`modbus_register_map.csv` 中的地址是协议 PDU 的 0 基地址，部分 HMI 会显示为 40001 起始地址。

## 上线前检查

- 本通用参考版要求目标 CPU 支持 64 位浮点 `LREAL`；Easy521 应改用已单独验证的 `easy521/` REAL32 版，不得机械地全局替换数据类型。
- 核对 Modbus 的寄存器地址偏移、16 位字节序和 64 位字顺序。这里沿用 Python：网络字节序 big-endian，不交换 16 位寄存器顺序。
- 用强制/仿真依次验证正常、低压、正向过载、反向过载、SOC 上下限和通信失效。
- 5 ms 任务的最坏执行时间必须小于 5 ms，并配置任务超时/看门狗。
- 实机变流器仍须保留自身的视在功率、电流、SOC、温度和故障联锁。该上层算法不能替代设备保护。

在工程目录执行 `python3 plc/verify_reference.py` 可复核参数及正常、低压、正/反向过载、SOC 闭锁、50 ms 锁存/恢复和在线辨识基准结果。该脚本是算法验收向量，不替代目标 PLC 编译器检查和硬件在环测试。

执行 `python3 plc/tests/run_full_validation.py` 可运行完整离线测试，包括逐状态差分、严格浮点边界、Modbus 编码和六阶段闭环场景。完整结论见 `VALIDATION_REPORT.md`。

## 与原 Python 的边界

PLC 中已迁移的是实时控制器。Tkinter 界面、CSV 记录和 `pymodbus` 客户端属于上位机功能，不应放进实时算法功能块；可分别由 HMI/SCADA、数据记录服务和 PLC 厂商的 Modbus 通信库承担。
