# 31. 真实 MatIEC 前端到 Linux ARM 对象（2026-10-19）

本轮在 Linux 中实际运行固定版本的 MatIEC，将含 `IF/ELSE` 的 ST 转为 C，再由 Clang 20 生成 ARM Thumb `MAIN.o`。这一编译路径使用真实 IEC 前端，不使用先前单行 ST 探针的表达式正则或手写 LLVM IR。同一份生成 C 和适配器在 Linux x86 上执行通过；本记录没有进行设备访问或 ARM 指令执行，完整应用绑定和真机验收分别记录。

工具为 [compile_easy521_matiec_probe.py](../../../tools/compile_easy521_matiec_probe.py)，机器可核验的来源、生成文件、实际依赖、命令、对象结构与 SHA-256 见[配套 JSON](31-matiec-native-arm-2026-10-19.json)。不把 `MAIN.o` 称为完整应用或固件。

## 本次输入和结果

输入是 ST **语句正文**，工具添加一个仅声明既有外部 `g_rBESS_Pcmd : REAL` 的 PROGRAM。测试正文如下，任务周期由既有应用配置决定，工具不生成调度器；该 REAL 的业务单位和允许范围由调用项目定义。本例仅验证阈值及分支，未涉及物理 I/O，也不声明保持属性或初值。

```iecst
(* 每次调用先读取既有 REAL；低于 10 时加 1，到达阈值后清零。
   外部变量不由适配器初始化；本例无停止、故障或物理输出逻辑。 *)
IF g_rBESS_Pcmd < REAL#10.0 THEN
    g_rBESS_Pcmd := g_rBESS_Pcmd + REAL#1.0;
ELSE
    g_rBESS_Pcmd := REAL#0.0;
END_IF;
```

将增加量改成 `REAL#2.0` 后重新运行同一条前端/后端路径，源码与 ARM 对象哈希均改变。两次正式离线构建分别保存在私有目录 `.plcidf/research-31-matiec-20261019/frozen-plus1/`、`frozen-plus2/`；早期试验目录保留。下表的大小是 `MAIN` 函数字节数，不是整个对象文件大小。

| 正文变化 | `MAIN` | 对象 SHA-256 |
| --- | ---: | --- |
| 阈值 10，低分支加 1 | 52 字节 | `ed414bd6e1f47818cdfd01407587c696ab237dc26d024cba37581f867a66e34f` |
| 阈值 10，低分支加 2 | 52 字节 | `a72912acd3fbd11bcb57b215540775ce17119926f02d2a362053ca06f031068e` |

对加 1 版本，将同一生成 `POUS.c` 与同一 `wrapper.c` 交给 Linux GCC，初值 `-1/0/9/10/100` 的一次调用结果为 `0/1/10/0/0`；从 0 连续调用 12 次得到 `1…10,0,1`。这验证真实生成 C 的两个分支，以及逐次调用没有把外部 REAL 重置为零。它仅是 x86 语义执行证据。

## 来源和构建依赖

MatIEC 固定提交为 `3a41303bcb4e50403417bc39c63f88981d7d961e`，本机安装目录 `/home/zss/.cache/plcidf/matiec`，实际 `iec2c` SHA-256 为 `4bd3cd311d03954a7cb6b834dc848829f6d9fcc79054f592525eeb8c42df3834`。工具核对安装清单的提交、编译器及每个库文件哈希，将库与 ST 输入冻结到新的输出目录，保留 MatIEC 原样生成的 `POUS.c`、`POUS.h` 和诊断。[MatIEC 固定源码](https://github.com/beremiz/matiec/tree/3a41303bcb4e50403417bc39c63f88981d7d961e)是类型和生成代码的来源。

Clang 为 root 从 Ubuntu 官方包安装的 `clang-20`；记录使用解析后的 ELF 二进制及 SHA-256。目标明确设为 `armv7a-none-eabi`、Cortex-A8、Thumb、NEON/VFPv3、`softfp`、`-ffixed-r9`、freestanding，未使用 fast-math。编译目标和目标库是不同依赖；不能因为 Clang 能交叉编译就假定主机 libc 可用于 PLC。[Clang 官方交叉编译说明](https://clang.llvm.org/docs/CrossCompilation.html)说明这些边界。

本次用 `-MD` 记录实际 C 依赖，包含系统头。每个依赖必须属于**编译前**已记录的冻结输入或 Clang resource headers，否则构建失败。正式构建各观察到 29 项依赖：适配器、生成 C/header、MatIEC 库头，以及 Clang 自带的 `limits.h`、`float.h`、`stdint.h`、`stddef.h`、`stdarg.h` 及其内部头；没有使用 `/usr/include` 或 ARM libc/sysroot。工具记录全部 265 个 Clang resource `.h` 的编译前哈希、实际依赖列表及编译后的复核。库/工具/生成文件/对象发生漂移会保留失败报告。

## ABI 适配和明确范围

适配器仅导出 `void MAIN(void)`，每次调用初始化栈上的 MatIEC 外部指针与 flags，然后执行未经改写的生成 C 正文。REAL 读写通过固定外部符号 `G_RBESS_PCMD`，适配器保留易变访问，**不写外部初值**。无强制变量、持久局部状态或定时器语义。

对象必须是 ELF32、小端、ARM EABI5、ET_REL；唯一全局定义为 `MAIN`，唯一未定义符号为 `G_RBESS_PCMD`，重定位仅接受该固定接口的 Thumb MOVW/MOVT 和 PREL31。额外运行库、libc、数学函数或其他未定义符号均拒绝。`readelf -A` 实测要求 R9 Unused、Thumb-2、Application profile 和 VFPv3，并拒绝 VFP 寄存器传参属性；其含义依据 [Arm ABI build attributes](https://github.com/ARM-software/abi-aa/blob/main/addenda32/addenda32.rst)。这些是结构约束，不是运行时 ABI 已接受的证明。

现有 [GVT/CSV 绑定研究](21-linux-existing-global-bindings-2026-10-19.md)负责实际变量地址；[完整应用配置](28-fixed-application-config-2026-10-19.md)和应用组装必须另行核对。本工具不替调用者分配变量、创建任务或生成下载配置，也不默认任意机型拥有这个 REAL。

当前接受范围为固定外部 REAL 的无状态赋值、普通受限表达式及 `IF/ELSE`。MatIEC 负责 ST 语法、类型、名称和 C 生成，工具检查生成 C 的接口和限定控制形式后原样编译。`WHILE`、额外局部变量、函数块和数学 helper 等当前拒绝；这不是任意 IEC 61131-3 程序编译支持。MatIEC 的实际 C/REAL 浮点语义也不保证与厂商先扩宽再运算的 helper 完全相同。上游头中未使用的变参 helper 存在 Clang 告警，因此不将整个运行库描述为已完成移植；当前产物没有这些运行库引用。

## 使用和验证

以下命令只创建本地新目录，不包含设备参数。输入文件保存上面的 ST 正文；输出目录必须不存在。每个外部进程有 0.1–30 秒的显式超时且无重试，失败时保留已生成文件和报告。

```bash
.venv/bin/python tools/compile_easy521_matiec_probe.py \
  --source /absolute/path/body.st \
  --matiec-root /home/zss/.cache/plcidf/matiec \
  --clang /usr/bin/clang-20 \
  --output-dir /existing/parent/new-build
```

16 项新增测试实际运行本机 MatIEC/Clang：正常 ST、两分支、连续调用、正文变化、语法/名称失败、拒绝不支持的生成代码、前端超时不重试、冻结输入漂移和未记录依赖拒绝。与既有原生编译测试合跑为 31 通过、5 跳过；跳过的是需要另行提供 llc 路径的既有测试，新增 16 项均执行。测试命令及 JUnit 哈希见配套 JSON。

本记录完成的是更真实的 Linux 编译前端及其可审计来源链。应用完整性、物理控制、设备运行效果和新的 ARM 对象真机验收必须分别保留实测记录，不能从这里的 x86 结果推定。
