# 储能自适应控制器 PLC 移植验收报告

> **适用范围：** 本报告验证的是 `LREAL/USINT` 通用 IEC 双精度参考版。已确定的目标设备为汇川 Easy521-0808TN，其 AutoShop LiteST 工程应使用 [`easy521/`](easy521/) 专用版；目标版验证见 [`easy521/VALIDATION_REPORT.md`](easy521/VALIDATION_REPORT.md)。

验收日期：2026-09-18  
被测对象：`FB_BESS_AdaptiveSupervisor.st`、`PRG_BESS_Control.st`、`GVL_BESS_IO.st`  
需求基线：`ieee33_bess_adaptive_R2022b.slx`、`模型框图.docx`、`modbus_realtime.py`

## 1. 验收结论

| 层级 | 结论 | 说明 |
|---|---|---|
| 控制算法等价性 | 通过 | 34 个被控制器使用的参数、8 个输出及 20 个状态与 Python/SLX 参考一致 |
| 功能与边界行为 | 通过 | 四种模式、严格门限、SOC 闭锁、锁存/恢复、辨识接收与拒绝均通过 |
| 长序列稳定性 | 通过 | 固定随机种子的 100,000 周期差分测试无偏差，未出现非有限输出 |
| 闭环功能仿真 | 通过 | 采用 SLX BESS 方程的 6 秒闭环场景通过 |
| Modbus 数据契约 | 通过（静态） | 0～35 连续寄存器、9 个 binary64 信号及大端往返检查通过 |
| 标准 ST 静态检查 | 通过 | 结构、块、括号、声明、关键状态及参数完整性通过 |
| 目标厂商 IDE 编译 | 通用版不适用；目标版另已通过 | 本通用版不是目标导入版；2026-09-28 Easy521 LiteST 原生工程已零错误编译并通过厂商模拟器，见 easy521/HARDWARE_COMMISSIONING_REPORT.md |
| PLC 实机周期/WCET | 待执行 | 必须在目标 CPU 上证明 5 ms 任务不超时 |
| Modbus 厂商功能块联调 | 待执行 | 当前已给协议和逻辑变量，尚无指定厂商通信库 |
| HIL/实际变流器联调 | 待执行 | 软件仿真不能替代保护、网络故障和实际设备测试 |

因此，本次结论是：**控制算法移植与离线模拟验收通过；在完成目标 PLC 编译、通信适配、任务时间测试和 HIL 前，不能判定整套现场系统已经具备投运条件。**

## 2. 需求追溯

| 编号 | 需求 | 实现/证据 | 结果 |
|---|---|---|---|
| R-01 | 控制周期 5 ms | SLX Rate Transition、Controller State Unit Delay 和 Word 均为 0.005 s；`PRG_BESS_Control` 要求 5 ms 周期任务 | 通过 |
| R-02 | 输入 Vpu、Pline、Qline、loading、SOC、Pess、Qess | FB 的 7 个 `LREAL` 测量输入 | 通过 |
| R-03 | 输出 Pcmd、Qcmd、mode 和辨识诊断 | FB 的 8 个主要输出及 Ready 状态 | 通过 |
| R-04 | 20 维 Unit Delay 状态 | FB 中 20 个持久状态逐项映射，扫描末状态供下一扫描使用 | 通过 |
| R-05 | 在线辨识窗口为 10 个周期 | `C_WINDOW=10`，每 50 ms 最多更新一次 | 通过 |
| R-06 | sVP/sVQ、kP/kQ 样本判据、滤波、限幅及置信度 | 与 MATLAB Function/Python 运算顺序一致 | 通过 |
| R-07 | Vpu < 0.95 进入低压，恢复带滞回和连续计数 | `C_VON=0.95`、`C_VOFF=0.975`、`C_RECOVER=10` | 通过 |
| R-08 | 低压优先无功，95% Qmax 持续 50 ms 后允许有功 | `C_QREACH=0.95`、`C_DWELL=10`，Qcmd 和 Qess 必须同时达标 | 通过 |
| R-09 | 正向/反向过载方向、投入/退出滞回 | `LON=1.0`、`LOFF=0.95`，方向状态为 +1/-1 | 通过 |
| R-10 | SOC 裕度闭锁 | 放电要求 `SOC > 0.12`；充电要求 `SOC < 0.88`，均为严格比较 | 通过 |
| R-11 | 指令限幅 | `Pcmd` 在 [-0.8, 0.8]，`Qcmd` 在 [0, 0.4] | 通过 |
| R-12 | 模式 0/1/2/3 | 正常、低压、正向过载、反向过载全部覆盖 | 通过 |
| R-13 | 保持现有 Modbus 寄存器协议 | 输入 0～27，输出 28～35，binary64 大端、不换字 | 通过（静态） |
| R-14 | 通信无效时安全输出 | `xInputValid=FALSE` 时 P/Q 指令归零、状态冻结 | 通过（FB 层） |

`CONTROLLER_P[0]=0.005` 是采样周期，只用于任务配置，原 MATLAB/Python `control()` 未在公式中读取它；其余 34 个参数均已逐项比较。

## 3. 人工逐行审查结果

### 3.1 状态映射

| Python/SLX 状态 | ST 状态 | 作用 |
|---|---|---|
| state[0..4] | `lrLow`～`lrQLatched` | 低压、恢复、过载方向和 Q 锁存 |
| state[5..10] | `lrAnchorV`～`lrEstimatorCount` | 辨识窗口锚点与计数器 |
| state[11..14] | `lrSVP`～`lrKQ` | 在线估计量 |
| state[15..18] | `lrCVP`～`lrCKQ` | 四个置信度计数器 |
| state[19] | `lrInitialized` | 首次有效扫描初始化标志 |

人工确认了以下容易出错的顺序：

- 辨识更新发生在本周期状态机计算之前，因此本周期立即使用新估计值。
- sVQ 在 sVP 之前更新；同一窗口更新 sVP 时使用已经更新的 sVQ，与原代码一致。
- 低压判据优先于过载模式；低压时可叠加正向过载的 P 指令约束。
- `qLatched` 一旦置位，在低压退出前保持，不会因单次 Q 降低而立即解除。
- 当过载方向存在但 SOC 不允许动作时，原算法输出 `mode=0`，ST 保持了这一语义。
- 所有 `<`、`>`、`<=`、`>=` 边界与原 MATLAB/Python 保持一致。

### 3.2 相对原 Python 的有意增强

ST 增加了 `xEnable`、`xReset`、`xInputValid`。它们位于算法外层，不改变有效扫描时的原始控制结果：

- Reset：复现 Simulink Unit Delay 的全零初始条件。
- Disable/Input invalid：输出零指令并冻结全部 20 个状态，防止坏数据污染辨识器。
- 通信恢复后应先 Reset 一次，再 Enable。

## 4. 自动与模拟测试

执行命令：

```bash
python3 plc/verify_reference.py
python3 plc/tests/run_full_validation.py
```

实际结果：

```text
PASS: PLC constants and controller acceptance vectors match the Python/SLX reference.

Ran 16 tests in 5.311s
OK
Executed 16 test methods including 100,000 differential scans in 5.312 s
```

### 4.1 定向测试覆盖

- 正常、低压、正向过载、反向过载四种模式。
- VON、VOFF、LON、LOFF 的等于、上一浮点数、下一浮点数边界。
- SOC 放电门限和充电门限的严格比较。
- Q 饱和持续满足时第 10 次调用锁存；不满足时计数下降而非清零；Qcmd/Qess 双条件。
- 低压连续 10 周期恢复。
- 有功主导和无功主导辨识窗口。
- DMIN、DVMAX、样本上下限和 K 上下限拒绝路径。
- loading=0、线路功率=0、EPS 附近的除零保护。
- Reset、Disable、Input invalid 的归零与状态冻结。
- binary64 大端寄存器往返以及寄存器连续性。

### 4.2 100,000 周期差分测试

使用固定随机种子 `0xB3552026`，每周期在以下范围生成有限输入：

- Vpu：0.82～1.10
- Pline：-1.8～1.8 pu
- Qline：-0.8～0.8 pu
- loading：0～1.6
- SOC：0～1
- Pess：-0.8～0.8 pu
- Qess：-0.4～0.4 pu

每个周期比较：Pcmd、Qcmd、mode、sVP、sVQ、kP、kQ、confidence 和全部 20 个状态。四种模式均实际出现，差分误差未超过 `2e-12`，指令及全部估计量始终在设计限幅内。

### 4.3 六阶段闭环仿真

仿真采用 SLX `BESS Step` 的原始方程和参数：Ts=5 ms、Srated=1 pu、Pmax=0.8、Qmax=0.4、SOC 初值 0.6、E=0.5 MWh、效率 0.95、τP=100 ms、τQ=50 ms、Sbase=2 MW。总时长 6 s，共 1,200 周期。

| 阶段 | 模式统计 | 关键结果 |
|---|---|---|
| 正常 1 s | mode 0: 200 | Pcmd=Qcmd=0 |
| 低压 1 s | mode 1: 200 | Vpu 从 0.910000 提升，最高 0.979829；Qcmd 达 0.4；Pcmd 最高 0.249502 |
| 恢复 1 s | mode 1: 9，mode 0: 191 | 第 10 个健康周期（50 ms）退出低压 |
| 正向过载 1 s | mode 2: 200 | Pcmd≈0.321291；稳定后 Pline 最低约 0.928724 pu |
| 正常 1 s | mode 0: 200 | 指令归零，BESS 一阶回落 |
| 反向过载 1 s | mode 3: 200 | Pcmd≈-0.321291；稳定后 Pline 最高约 -0.928724 pu |

最终 SOC=0.599711，始终位于 [0.1, 0.9]。

## 5. 发现项与剩余风险

| 编号 | 等级 | 发现/风险 | 处理或投运要求 |
|---|---|---|---|
| F-01 | 高 | Python 使用 `root.after(20, ...)`，实际至少 20 ms，且注释误写 1 s；与 SLX/Word 的 5 ms 不一致 | PLC 版按 5 ms 实现；不得照搬 Python 定时器 |
| F-02 | 高 | 目标已确定为 Easy521/AutoShop；通用版的 `LREAL/USINT` 不适用于 LiteST | 已单独生成 `easy521/` REAL32 版；仍需在 AutoShop 中做零错误编译 |
| F-03 | 高 | Modbus 客户端功能块尚未实现；当前只有算法、逻辑 I/O 和寄存器契约 | 使用厂商异步 Modbus 库，整帧发布测量快照，不得在 5 ms 算法中阻塞等待网络 |
| F-04 | 高 | FB 输出零指令并不保证通信故障时变流器收到零值；写失败可能令设备保持旧指令 | 变流器侧必须配置命令超时/心跳和本地保护 |
| F-05 | 中 | `xInputValid` 是外部数据质量契约；通用 IEC ST 中没有采用厂商专用 `ISFINITE` | 通信层必须拒绝 NaN/Inf、越界量和半帧数据；可在确定厂商后增加本地诊断 |
| F-06 | 中 | PLC 热启动/掉电后的 FB 状态保持方式取决于平台 | 明确非保持/保持区配置；启动或重连后强制 Reset |
| F-07 | 低 | LREAL 中 `0.1+0.02` 为约 0.12000000000000001，SOC 判据又是严格 `>` | 测试已按实际 LREAL 阈值通过；HMI 显示应避免暗示等于 0.12 会放电 |
| F-08 | 中 | 没有 MATLAB/Simulink Runtime，未执行原 `.slx` 的整网联合仿真 | 当前按 SLX 中提取的控制器和 BESS 方程验证；最终做 SLX-SIL/HIL 联调 |
| F-09 | 高 | 上层算法不是设备保护 | 变流器仍须保留视在功率、电流、SOC、温度、绝缘及急停联锁 |

尝试采用独立开源 IEC 编译器进行额外编译检查，但当前环境缺少其构建所需的 autotools/flex/bison，且它也不能代表最终厂商方言。因此没有把静态检查表述为“PLC 已编译通过”。

## 6. 目标 PLC 上的最终验收清单

1. 在指定 IDE 中以零错误、零隐式窄化转换完成编译。
2. 确认目标 CPU 的 `LREAL` 为 IEEE-754 binary64，并核对 Modbus 4 个寄存器的字节/字顺序。
3. 测量 5 ms 任务的平均、最大和最坏执行时间；最大值必须留有工程裕量且小于 5 ms。
4. 注入 16 个定向测试和 100,000 周期差分测试中的固定向量，读取 FB 输出及内部状态比较。
5. 断网、超时、半帧、乱序、NaN、Inf、越界、PLC 热启动、变流器拒绝命令逐项故障注入。
6. 与 SLX 通过实际 Modbus 链路做 SIL，再接变流器控制器做 HIL。
7. 最后验证设备级保护、心跳、急停和人工/自动模式切换，完成投运签字。
