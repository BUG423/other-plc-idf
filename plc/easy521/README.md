# 汇川 Easy521-0808TN / AutoShop 专用版本

本目录是针对汇川 `Easy521-0808TN` 的 AutoShop LiteST 版本。不要把上一级通用 IEC 文件直接粘贴到 AutoShop：本控制器采用 `BOOL`、`INT`、`DINT` 和 32 位 `REAL`，不使用通用版本的 `LREAL` 和 `USINT`。

## 已建立的实际工程（2026-09-28）

Windows 上打开 `E:\PLCProjects\BESS_E521_DEV\BESS_E521_DEV.hcp`，即可继续算法工程开发；工作区完整副本在 [autoshop/BESS_E521_DEV](autoshop/BESS_E521_DEV/)。正式工程已通过 AutoShop V4.11.0.5 零错误编译，重开确认 5 ms 恒定扫描和 100 ms 看门狗。

[BESS_E521_TEST](autoshop/BESS_E521_TEST/) 是独立的**模拟器专用**验收工程，正确用例两轮均 28/28；错误期望值负向对照准确失败。测试与正式工程的原生 FB 文件完全一致。TEST 禁止下载到物理 PLC。

USB 真机为 Easy521 / 6.11.0.1，原 PLC 已备份。用户授权覆盖并确认隔离后，正式 DEV 已下载到真机，程序一致性校验通过；三次 RUN 请求均失败（最新 16:13:26），监控仍为 STOP、故障诊断为 0 个。当前没有真机算法运行成绩，DEV 未绑定实际通信及现场 I/O，不能直接投运。方法、原始结果和限制见 [本次厂商验收报告](HARDWARE_COMMISSIONING_REPORT.md)。

[BESS_E521_BENCH](autoshop/BESS_E521_BENCH/) 为新建的隔离硬件台架工程，943 行 MAIN 零错误编译，尚未下载执行。源码由 [硬件台架生成器](tests/build_hardware_bench.py) 产生，使用相同 FB 和 28 组固定向量，但加上 M8002 首周期清理、D523=3 区分旧模拟器数据，以及完成后持续复位、禁用、指令归零。只允许在隔离台架使用，测试后应恢复禁用 DEV；不能现场投运，也不能把其编译成功当作硬件测试通过。

## AutoShop 建立步骤

1. 在 AutoShop 新建工程，系列/型号选择 `Easy500 / Easy521`，主程序选择 ST。
2. 新建功能块 `FB_BESS_AdaptiveSupervisor_Easy521`，语言选择 ST。
3. 按 `FB_BESS_AdaptiveSupervisor_Easy521_variables.csv` 在功能块变量表中建立输入、输出和局部变量。状态变量按 CSV 设为“掉电不保持（No）”，启动时由 Reset 建立确定状态。
4. 将 `FB_BESS_AdaptiveSupervisor_Easy521_body.st` 的内容粘贴到该 FB 的 LiteST 正文。
5. 建立一个 `FB_BESS_AdaptiveSupervisor_Easy521` 类型的实例 `fbBESS`，并按 `MAIN_5ms_variables.csv` 建立全局变量。
6. 将 `MAIN_5ms_body.st` 粘贴到主程序。
7. 把主程序执行方式设为恒定周期，周期设为 `5 ms`，同时设置合适的看门狗。在线监视 CPU 占用率和实际扫描周期。
8. 首次运行：先保持 `g_xBESS_Enable=FALSE`，令 `g_xBESS_Reset=TRUE` 至少一个周期；然后清除 Reset，整帧测量有效后置 `g_xBESS_MeasurementsValid=TRUE` 和 Enable。

两个 `*_variables.csv` 是建表清单。`autoshop_global_import.csv` 与 `autoshop_fb_import.csv` 则依据本次 AutoShop 真实模板生成，并已成功导入；工作区为 UTF-8，AutoShop 4.11 Unicode 导入使用 UTF-16。不同软件版本须重新核对原生格式。

CLI 可用于编辑这些文本、版本管理和执行下文的离线回归测试；目标编译、硬件配置、下载和在线 Trace 仍须使用 AutoShop。

## 必须更改 Modbus 协议

原 Python 使用 64 位 double，每个量占 4 个保持寄存器。Easy521 LiteST 不支持 `LREAL`，所以本版本采用 32 位 IEEE-754 `REAL`，每个量占 2 个寄存器，地址见 `modbus_register_map_real32.csv`。

推荐协议为大端 ABCD：例如 `1.0` 的字节为 `3F 80 00 00`，寄存器 0 为 `16#3F80`，寄存器 1 为 `16#0000`。在联调时必须用 1.0、-1.0、0.95 三个已知值验证字节序和字顺序。

如果 Simulink/PC 端的 64 位协议不能修改，不建议在 Easy521 内手工实现 binary64。应增加 PC 协议网关，将 64 位 double 转换为 32 位 REAL 后再与 PLC 通信。

## 通信与控制任务

Modbus TCP 通信不能阻塞 5 ms 控制程序。通信逻辑在完整读到 14 个输入寄存器后，一次性更新 7 个测量变量，再置 MeasurementValid；控制任务始终使用最近一个完整快照。通信超时或数据异常时立即清除 MeasurementValid。

PLC 给出零指令不等于变流器一定收到了零值，因此设备侧仍必须配置通信心跳、指令超时和本地保护。

## 数据有效性

AutoShop LiteST 没有采用厂商无关的 `ISFINITE` 检查。通信层需要拒绝 NaN、Inf、半帧数据和明显越界值。建议初始工程采用以下合理性范围，再根据现场量程调整：

- `0.5 <= Vpu <= 1.5`
- `-2.5 <= Pline,Qline <= 2.5`
- `0.0 <= loading <= 2.0`
- `0.0 <= SOC <= 1.0`
- `-1.0 <= Pess,Qess <= 1.0`

## 已完成及尚需完成的验证

- 已完成：实际目标编译 0 错误、厂商离线模拟器 28 组逐扫描用例及错误对照。
- 尚需：任何通信接口改动后的重编译与回归。
- 用 Trace 确认主程序实际为 5 ms，且 CPU 占用和看门狗有足够裕量。
- 在真机上验证 Modbus 客户端/服务器角色、连接超时、重连和字序。

## 离线测试

在项目根目录执行：

```bash
python3 plc/easy521/tests/run_easy521_validation.py
```

当前结果为 9/9 通过，包括数据类型兼容性、REAL32 Modbus 编码、四种工作模式、100,000 组单扫描精度比较、100,000 扫描长序列和 6 秒闭环模拟。详细结果见 `VALIDATION_REPORT.md`。

28 组厂商验收 MAIN 由 `tests/build_vendor_acceptance.py` 生成，每次主扫描只调用 FB 一次。`--check` 仅预检查固定期望值，不代表模拟器已执行。真实结果在 `evidence/vendor_sim_*.json`。无功饱和计数不满足时递减而非清零；Ready 只表示已初始化，不能替代输入有效位。
