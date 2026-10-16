# wsl-tools — py2plc Modbus 工具链（WSL 软件闭环）

在真机（汇川 Easy521）接入之前，把**软件侧全链路**先在 WSL 里闭环：
一个"虚拟 Easy521"Modbus TCP 从站 + 一个只读监测客户端 + pytest 测试。

```
┌──────────────────────────┐         Modbus TCP (127.0.0.1:1502)        ┌─────────────────────────┐
│  virtual_easy521.py      │  ◄──── FC01/FC02/FC03/FC04 只读 ─────────  │  e521_monitor.py        │
│  虚拟从站 + 场景播放器    │                                            │  只读监测 / 字序校准     │
│  (steady/low_voltage/    │                                            │  (TCP / 串口 RTU 双传输) │
│   overload) + JSONL 日志 │                                            │  表格 + JSONL 落盘       │
└──────────────────────────┘                                            └─────────────────────────┘
        共享契约：e521_regs.py（寄存器布局 / REAL32 ABCD·BADC 编解码 / 字序自动判别）
```

## 0. 环境

- Python 3.12，虚拟环境 `/home/zss/workspace/py2plc/.venv`（已装 pymodbus 3.16.1、pyserial、pytest）
- 所有网络只绑 `127.0.0.1`，不碰局域网
- 下文命令都假设已激活 venv：`source /home/zss/workspace/py2plc/.venv/bin/activate`
  （或直接用 `.venv/bin/python` 全路径）

## 1. 起虚拟从站

```bash
cd /home/zss/workspace/py2plc/wsl-tools

# 场景一：稳态（Vpu=1.0 缓波纹、SOC 缓降、Pcmd/Qcmd 基础调度值）
python virtual_easy521.py --scenario steady

# 场景二：低压（Vpu 1.0 → 0.91 保持 10s → 恢复，循环；0.91 < VON=0.95 触发算法低压模式）
python virtual_easy521.py --scenario low_voltage
python virtual_easy521.py --scenario low_voltage --hold-dip 15   # 自定义保持时长

# 场景三：过载（loading 越限正向 8s → 反向 8s 交替；Pline 符号决定方向）
python virtual_easy521.py --scenario overload
```

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port` | 1502 | 监听端口（真机是 502，这里默认 1502 免 root） |
| `--word-order` | abcd | REAL32 字序：`abcd`=契约大端直排；`badc`=字交换（备选） |
| `--tick` | 0.1 | 场景刷新节拍（秒） |
| `--log FILE` | 关 | JSONL 运行日志（时间戳 + 寄存器快照 + 解码值），`--log-interval` 控制频率 |
| `--quiet` | 关 | 不打印每秒状态行 |

启动后打印 `READY` 即可连接；`Ctrl+C`（或 `kill`）干净退出。

**只读面**：FC01 线圈 / FC02 离散输入 / FC03 保持寄存器 / FC04 输入寄存器；
任何写功能码（FC05/06/15/16 等）一律回异常响应（与"上位机对 PLC 只读"边界一致）。
站号不校验（对齐 Easy521 手册行为）。状态位语义：bit0 运行、bit1 低压、
bit2 正过载、bit3 反过载、bit4 心跳；D18 = 工作模式（0 正常 / 1 低压 / 2 正过载 / 3 反过载，
虚拟机扩展，便于观察）。

## 2. 监测（这就是将来直连真机的工具）

```bash
# 单次 + 字序自动判别（推荐的首连方式）
python e521_monitor.py --once --calibrate

# 持续监测，1s 一轮，表格 + JSONL 落盘
python e521_monitor.py --interval 1 --jsonl mon.jsonl

# 同时读线圈/离散输入（仍只读）
python e521_monitor.py --once --with-bits
```

输出示例（low_voltage 场景 0.91pu 保持段）：

```
=== Easy521 监测 @ tcp://127.0.0.1:1502  unit=1  2026-10-16T05:41:48.989+00:00
--- 字序: ABCD（大端直排，reg0=高字，项目契约）
量名        寄存器       原始字(w0,w1)        解码值  单位   范围检查
Vpu       0-1       (0x3f68,0xf5c3)     0.91000  pu   ok  # 节点电压标幺值
...
Qcmd      16-17     (0x3eb3,0x3333)     0.35000  pu   ok  # 储能无功指令
D18(mode) 18        -                       1   low_voltage 低压
FC01 线圈 0~7 : 11000000   (bit0运行 bit1低压 bit2正过载 bit3反过载 bit4心跳)
```

`--calibrate` 会把 9 个量分别按 ABCD/BADC 两种字序都解一遍，按
**有限性 + 非微小垃圾值 + 契约合理范围** 打分（满分 9.0），打印对照表与结论；
得分并列（如全零寄存器）时如实报告"无法判别"，默认回落 ABCD。
手动覆盖：`--word-order badc`。

严格只读是硬边界：整个 `e521_monitor.py` 只出现 `read_holding_registers` /
`read_coils` / `read_discrete_inputs` / `read_input_registers` 四个读调用，
测试里有专门的用例锁死这一点（任何写调用直接失败）。

## 3. RS485 串口模式（USB-485 适配器场景）

Easy 系列手册载明 RS485 口支持 Modbus RTU。将来给 PLC 的 **RS485 口**接一只
USB-RS485 适配器进 WSL（设备通常枚举为 `/dev/ttyUSB0`），监测工具零改动复用：

```bash
# 默认 9600 8N1（Easy 系列 RTU 常见配置，以实测为准）
python e521_monitor.py --transport serial --device /dev/ttyUSB0 --once --calibrate

# 波特率/校验按实测调整
python e521_monitor.py --transport serial --device /dev/ttyUSB0 \
    --baud 115200 --parity N --stopbits 1 --interval 1 --jsonl serial.jsonl
```

说明：

- 串口模式走 `ModbusSerialClient`（RTU 帧），读功能码、REAL32 解码、字序校准、
  JSONL 格式与 TCP 模式**完全同一套代码**（`--transport` 只切换传输层）。
- 严格只读约束对串口模式同样生效。
- 无硬件时的回归：`tests/test_serial_mock.py` 用假客户端验证了参数装配（9600 8N1
  默认）、只读调用集、JSONL 落盘与失败退出码。
- 定位备注（2026-10-16 真机实测）：Easy521 的**编程 USB 口**（CDC 虚拟串口）
  经两轮 15 组合探测（5 波特×2 站号 8N1 + 8E1/8N2）全部零应答——固件不在编程口
  上应答标准 RTU，USB 直连假说已证伪（证据存档 `wsl-probe/usb-rtu/`）。
  因此串口模式的唯一用途是 **RS485 + USB-485 适配器** 这条路，不是 USB 直连。

## 4. 测试

```bash
cd /home/zss/workspace/py2plc/wsl-tools
python -m pytest tests/ -v          # 65 项：契约/往返/判别 + 串口 mock + 端到端
```

- `test_real32.py` — REAL32 编解码往返（ABCD/BADC 各自，按 float32 位型比较）、
  已知位型钉死、契约偏移、未知字序拒绝
- `test_calibrate.py` — 字序自动判别正确性（含 2 的幂附近最坏情形、全零退化、
  单量损坏、随机垃圾）
- `test_serial_mock.py` — 串口模式参数装配 + 只读管线（mock，无硬件依赖）
- `test_e2e.py` — 起真实从站子进程（127.0.0.1 随机端口）：low_voltage 跌落/恢复
  两相位断言、BADC 从站端到端判别、从站拒绝写、JSONL 日志、overload 场景

## 5. 将来如何切到真机（唯一要改的参数）

软件层零改动，只换命令行参数：

```bash
# 1) 首连：把 --host 改成 PLC 的 IP，--port 改 502，先 --calibrate
python e521_monitor.py --host <PLC_IP> --port 502 --once --calibrate

# 2) 确认字序后持续采集
python e521_monitor.py --host <PLC_IP> --port 502 --interval 1 --jsonl real.jsonl

# 若不走网口：RS485 口 + USB-485 适配器（见第 3 节）
python e521_monitor.py --transport serial --device /dev/ttyUSB0 --once --calibrate
```

| 参数 | 虚拟从站 | 真机 Easy521 |
|---|---|---|
| `--host` | 127.0.0.1 | PLC 的 IP |
| `--port` | 1502 | **502**（手册：Modbus TCP 从站默认开启） |
| `--transport` | tcp | tcp；或 RS485 口 + USB-485 适配器时 serial |
| 字序 | abcd（契约） | 未知 → 先 `--calibrate`，异常再 `--word-order badc` |
| 站号 | 任意（从站不校验） | 任意（手册：不校验站号），默认 `--unit-id 1` |

注意：真机接入前先在 PLC 侧确认 D0~D17 已按
`plc/easy521/modbus_register_map_real32.csv` 契约映射（FC03 地址 0~17 与
D0~D17 一比一），并保证上位机只做读操作。

## 6. 文件清单

```
wsl-tools/
├── e521_regs.py          # 契约唯一定义处：量表/编解码/字序判别
├── virtual_easy521.py    # 虚拟 Easy521 从站（场景播放 + 只读面 + JSONL 日志）
├── e521_monitor.py       # 只读监测客户端（TCP + 串口 RTU、表格 + JSONL）
├── README.md             # 本文件
└── tests/
    ├── conftest.py
    ├── test_real32.py
    ├── test_calibrate.py
    ├── test_serial_mock.py
    └── test_e2e.py
```

实现备注（pymodbus 3.16.1 相关，升级时需复查）：

- 客户端读调用用 `device_id=` 传站号（3.10 起 `slave=` 更名）。
- 从站数据面用 `SimDevice` + 4 个只读 `SimData` 块；server 必须在承载它的事件
  循环线程内构造；场景线程通过 `SimRuntime.block[fc][2]` 活列表直写运行时值。
- 位块（FC05/15）在库内不受 `readonly` 约束（`__create_block_bits` 缺口），
  用 `SimDevice.action` 钩子统一拒绝全部写功能码兜底。
