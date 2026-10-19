# 真实MatIEC三BOOL程序与Easy521描述符shim：离线编译和host stub

2026-10-19。本轮新增 [compile_easy521_io_probe.py](../../../tools/compile_easy521_io_probe.py) 和 [专属测试](../../../tools/tests/test_compile_easy521_io_probe.py)，将真实MatIEC生成的三BOOL程序接到研究34恢复的运行库描述符调用。没有修改研究31的默认REAL profile、研究33的绑定器或旧候选，没有执行Windows、设备、Git、下载或物理I/O。源码、命令、工具组件、生成文件、实际依赖、对象和host stub结果见[机器记录](37-matiec-io-probe-2026-10-19.json)。本轮没有把新MAIN对象称为完整可运行应用。

**已实际完成：原灯程序ST → 固定MatIEC IEC2C → 未改写的生成C → 描述符shim → 新Thumb ARM对象。** 正常跟随时钟与反相两个版本得到不同对象；同一wrapper和原生成C在Linux主机上用自写运行库回调完成16个行为样本。新对象的三个helper全部为UND/global/NOTYPE，恰有3个 `R_ARM_ABS32` 和1个 `R_ARM_PREL31`；没有把位操作数或函数指针绑定为RELC变量。

## 1. 输入接口和支持边界

profile为 `matiec-easy521-three-bool-descriptor-io-v1`。工具为语句正文添加仅含以下接口的PROGRAM：

```iecst
VAR_INPUT
    M8002 : BOOL;
    M8013 : BOOL;
END_VAR
VAR_OUTPUT
    Y0 : BOOL;
END_VAR
```

这些是栈上MatIEC程序上下文中的BOOL快照和结果，不是新分配的PLC全局变量或物理地址指针。默认正文直接采用已有[灯例MAIN_body.st](../../../examples/easy521_led_blink/MAIN_body.st)，SHA-256 `6c51789d485457f9fc64ff34c2f0101f6c10db61bce1bd2a0f529c2c0111e7ed`：首RUN扫描写FALSE，后续复制M8013。任务/刷新周期继承调用者，本工具不生成调度器、定时器、保持状态或STOP处理。BOOL正常值域为FALSE/TRUE；host额外低字节样本仅验证原ABI消费方式。

MatIEC负责实际IEC语法、名称和类型处理。新验证器只检查其生成C：接口必须恰有三个BOOL；只允许受限BOOL表达式、IF/ELSE；每条执行路径必须恰好给Y0赋值一次。输入赋值、读取Y0旧值、额外变量/POU、循环、提前返回、其他helper及不完整/重复输出赋值均拒绝。生成C没有被转写为另一套表达式，也没有新增手写ST解析器。

## 2. 描述符与运行库调用

wrapper按研究34的顺序运行：

1. 通过READ_BITS读取M8002，取r0返回值的低8位作为BOOL快照。
2. 调用 `_HC_INTERRUPTFCBYST`，采用无有意义参数、32位返回的候选调用形状，并显式丢弃返回。
3. 仅非首扫描时，通过READ_BITS读取M8013；首扫描的本地M8013快照为0。
4. 执行原MatIEC生成正文。
5. 用WRITE_BITS描述符提交Y0一次，返回值显式丢弃，与观察到的厂商ARM调用方式一致，不能把丢弃返回说成写入成功。

read描述符12字节：三个32位槽为 `0xffffffff、编码操作数、1`。编码操作数仅允许已观察的 `M8002=0x06201802`、`M8013=0x06201822`；write描述符16字节，前三槽为 `0xffffffff、Y0编码0x00080000、1`，`+12`存一字节结果。`offsetof/sizeof`编译期断言保护字段偏移。

所有字段通过逐项volatile存储构造，避免引入尚未校准的`.rodata`或全局状态。write剩余三字节明确写本地零值。这是本shim的确定性局部策略，**不是已证明的厂商padding规范**：研究34只观察到ARM写低8位，而Simulator helper读16位；物理helper究竟消费多宽、如何处理错误/强制/刷新，仍unknown。前导`-1`的含义、数量字段的通用范围和CPU实际typedef同样未被臆造。

源码中的编码操作数只作为整数放进描述符；唯一传给helper的指针指向本地描述符。没有把 `0x06201822` 或 `0x00080000` 转成RAM/MMIO指针。

## 3. 函数指针分类和重定位先行验证

先用私有最小C试验确认：Clang普通聚合初始化会生成`.rodata`引用，因此最终wrapper改为逐字段volatile存储。函数地址通过空asm寄存器屏障保持间接调用，ARM编译增加 `-mno-movt` 以产生观察到的literal函数地址形式；没有修改对象符号表去伪造类型。

最终对象只导出全局Thumb函数MAIN，三个外部项为：

| 符号 | st_info | section/value/size | 实际重定位 |
|---|---:|---|---|
| `_HC_LD_DEV_MEM_READ_BITS` | `0x10` | UND / 0 / 0 | ABS32 |
| `_HC_LD_DEV_MEM_WRITE_BITS` | `0x10` | UND / 0 / 0 | ABS32 |
| `_HC_INTERRUPTFCBYST` | `0x10` | UND / 0 / 0 | ABS32 |

`0x10`是global/NOTYPE，与研究34真实设备对象类别一致；没有出现STT_OBJECT、FUNC import或普通全局绑定的RELC8。对象还带正常的 `.ARM.exidx → .text` PREL31。工具拒绝其他外部名称、额外全局、数据/BSS/rodata分配段和未观察的调用重定位类别。R9 Unused、Thumb-2、Application profile及非VFP寄存器传参属性均实际检查。

这些约束证明新对象形状接近既有调用合同，**不能单凭类别匹配证明物理loader或运行库接受**。物理调用实现、外部解析与最终应用验收仍归统筹独立处理。

## 4. 真实编译和host结果

固定MatIEC提交为 `3a41303bcb4e50403417bc39c63f88981d7d961e`，实际前端与Clang依赖沿用研究31的审计契约。新工具复用31的受限文件读取、命令运行和ELF解析组件；独立实现BOOL接口和对象验证，不运行31的REAL编译profile，也不偷偷替换其常量。

| 源码 | 私有稳定目录 | MAIN字节数 | ARM对象SHA-256 |
|---|---|---:|---|
| 首扫描FALSE，后续M8013 | `.plcidf/research-37-io-probe-20261019/frozen-clock/` | 116 | `cbf5e14210e01bb5129d4d74cfa3e51f5775b180101a404a2ae3f807883791b2` |
| 首扫描FALSE，后续NOT M8013 | `.plcidf/research-37-io-probe-20261019/frozen-inverted/` | 124 | `cccb82c5da748706413a036e55de7ffa29c9b122bb4b1550479484997a2a765c` |

反相变体同步修改了源码中的行为注释。两目录保留原ST、完整封装ST、原样生成C/header、wrapper、ARM对象、依赖文件和编译报告；早期callshape/clock-build试验保留，不冒充最终报告。

host stub以同一wrapper和生成C调用自写READ/HOOK/WRITE函数，检查三个操作数、前导字段、数量、write偏移及局部padding，并记录顺序。首扫描为 `R → H → W`，后续为 `R → H → C → W`，每次恰好一次Y0提交。覆盖首扫描/非首扫描、时钟低/高及返回值高位不为零的8组样本，两种程序共16组。

stub故意令hook返回 `0xdeadbeef`、writer返回99，确认wrapper确实丢弃它们；这只是调用策略测试，不是这些值在真机表示成功的证明。host执行不运行ARM指令，也未接入Simulator或物理PLC。

## 5. 审计、测试和下一阶段

工具记录并复核源文件、五个Python组件、MatIEC前端/安装清单/库头、Clang/readelf及Clang resource headers。`-MD`列出的每个实际C依赖都必须属于编译前已冻结文件或已快照的resource headers，拒绝未声明的主机sysroot/libc依赖。每个进程有0.1～30秒超时、仅执行一次；失败保留新目录和报告，不覆盖输入或旧结果。

```bash
.venv/bin/python tools/compile_easy521_io_probe.py \
  --source examples/easy521_led_blink/MAIN_body.st \
  --matiec-root /home/zss/.cache/plcidf/matiec \
  --clang /usr/bin/clang-20 \
  --output-dir /existing/parent/new-io-build

.venv/bin/python -m pytest -q tools/tests/test_compile_easy521_io_probe.py
```

24项测试通过，0跳过：真实前端/ARM编译、同C的host描述符与时序测试、规则变化、输入写/反馈/不完整或重复赋值/循环/额外变量拒绝、函数符号错误分类、额外重定位/分配段拒绝、超时不重试、输入漂移及未快照依赖拒绝。缺少实际MatIEC/Clang时相关测试会明确跳过，不能报告为真编译通过。

本轮仍为 `downloadable=false、runtime_abi_verified=false、arm_code_executed=false`。下一步可把这套新MAIN交给同一冻结运行库做独立两级链接，核对完整应用继承、函数解析和设备状态后，由统筹决定是否受控验收。工具没有写设备的入口，用户“输出悬空无负载”的确认也没有被转化成代理自行下装的权限。
