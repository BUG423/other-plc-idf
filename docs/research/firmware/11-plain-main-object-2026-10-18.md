# 普通 MAIN 正文到厂商 ARM 对象（2026-10-18）

已用完全自写的 `COUNTER := COUNTER + 1;` 正文和显式 `COUNTER:DINT` 声明，在不使用 AutoShop GUI 的情况下生成合法 STTemp 输入，并由原厂 StLang_New 编译出 2040 字节 ARM 对象。改为 `+2` 后对象哈希不同；错误赋值正文由真实编译器拒绝。

新增可复用工具 [prepare_vendor_st.py](../../../tools/prepare_vendor_st.py)，与既有 [compile_vendor_st.py](../../../tools/compile_vendor_st.py) 分别负责受限正文封装和原厂对象编译。没有执行前处理/通信 DLL 的未知 ABI，没有操作 Windows GUI、USB 或 PLC；本轮所有 Windows 输出均在此前隔离实验根目录下新建的子目录。

## 使用接口

输入 `body.st` 是 UTF-8 的 MAIN 语句正文，无 PROGRAM/VAR 包装。`globals.json` 必须使用如下结构，JSON 字段和变量名不允许重复：

```json
{"schema":"plcidf.vendor-main-source.v1","globals":{"COUNTER":"DINT"}}
```

```bash
python tools/prepare_vendor_st.py --source body.st --globals globals.json \
  --output-dir /mnt/e/PLCProjects/fresh_STTemp
python tools/compile_vendor_st.py \
  --compiler 'E:\Software\AutoShop\Bin\STLang_New\StLang.exe' \
  --input-dir 'E:\PLCProjects\fresh_STTemp' \
  --build-dir 'E:\PLCProjects\fresh_ARM_objects'
```

Python API 为 `prepare_vendor_st(source_path, globals_path, output_directory) -> dict`，输出目录必须不存在，父目录须已经存在；工具独占创建 `MAIN.st` 和空 `MAIN.cl`。输出 schema 为 `plcidf.vendor-main-preparation.v1`，scope 固定 `vendor-main-intermediate-only`，成功状态为 `prepared`。它记录两个原始输入逐字 SHA256、生成中间文本 SHA256 和两个输出的大小/哈希；写入结束再次读取输入和输出，变化即失败。`prepared` 表示中间输入准备完成，语法是否成立仍须由后续厂商编译器判定。

当前生成器为显式命名的 BOOL、INT、DINT、REAL 全局变量及基础语句生成中间输入，并注入编译器处理多语句/控制流所需的 `_HC_EMPTYFUC:VOID()` 声明。变量不是设备地址分配；它们将成为对象文件的外部符号，完整工程链接还须为其提供实际存储和运行绑定。声明限制为 1–256 项，输入各不超过 1 MiB；256 是资源上限，并非任意组合程序的编译保证。实际已验证单类型运算、混合四类型 IF、多语句和 256 个 DINT 声明案例。

工具拒绝软元件和 `%I/%Q/%M` 地址、厂商内部名、函数/库调用、数组、字段、扩展字面量、局部声明与完整 PROGRAM/FUNCTION 包装。词法先跳过嵌套块注释、行注释和引号内容；注释中的伪地址不会触发误判，字符串字面量会按“不支持字符串子集”报告。语法与类型的最终判断交给厂商编译器；此工具不模拟执行 ST。

## 新证据与边界

只读分析主机编译器的文件读取路径后，确认其跳过 32 字节容器头，再按固定字节格式还原预处理文本。对既有冻结的 1715 字节、用户自有中间输入进行格式往返核验，内容实际是全局声明、主程序语句及已经替换的软元件访问。由此实现独立封装；最初单 DINT 正例没有复用该旧程序的正文或对象；后续根代理独立复核暴露缺失的隐式运行库声明，修复见下节。

单条简单赋值的最小声明段为 `VAR_GLOBAL ... END_VAR`，其后直接接 MAIN 语句；当前生成器额外先输出固定的 `_HC_EMPTYFUC` 函数声明，以覆盖已复核的多语句和 IF；不需要把它改成 `PROGRAM MAIN ... END_PROGRAM`。新封装使用观察到的 32 字节头部版本组合，尚未验证其他版本、全工程文件种类或所有机型。对象目标仍只证明 ARM 可重定位格式，不能确认设备 SoC 或操作系统。

| 真实实验 | 准备 | 原厂对象编译 | MAIN.o SHA256 |
| --- | --- | --- | --- |
| `COUNTER := COUNTER + 1;` | prepared | verified，2040 B | `9d2063daa05318c463c27a43ae6895e79fe27ba6d34dadd08dc27eb0e22affd1` |
| `COUNTER := COUNTER + 2;` | prepared | verified，2040 B | `ef27685da39c95eedb3c942d64b2a3ee78ade4d50ce7897d73041213bf26add4` |
| `COUNTER := ;` | prepared | failed，捕获语法错误 | 不作为有效对象交付 |

两个正例均满足 IPC started/done/success、精确 Success 结果、有界完整诊断无错误、新 ARM ELF 和非空 MAIN 函数核查；对象包含尚待链接的 COUNTER 外部符号。原有空容器“先报 lexer error 后又报 success”的反例仍保持失败处理。准备工具 29 项测试与原编译工具 31 项测试合计 **60 项通过**，覆盖注释/字符串伪地址、已存在目录、符号链接、输入变化和不支持语法。

## 独立复核发现的崩溃与修复

根代理使用四个混合类型变量和 IF 的独立输入时，最初版本虽然生成了中间文件，但原厂编译器实际崩溃，返回 `0xc0000005`（有符号值 -1073741819），IPC 只有 started，没有 done/success；编译 worker 正确返回失败。初版只做了单 DINT 真实编译便扩大支持描述，证据不足，此处修正。

随后在独立新目录完成单 BOOL、INT、DINT、REAL、双变量和控制流矩阵：六个单变量简单赋值/运算案例通过；两变量单条赋值也通过；单变量两条赋值、两变量各赋值、单变量 IF 和混合四类型 IF 均崩溃。因此变量数量和 32 字节头计数不是该失败的解释。

在相同正文前补入 `FUNCTION _HC_EMPTYFUC : VOID ... END_FUNCTION` 声明后，上述多语句/IF 案例通过。该声明也出现在原先合法的用户中间输入中。当前工具固定注入它；它仅提供函数声明，定义仍须后续原厂运行库链接。本轮没有通过修改编译器状态或忽略错误来获得成功。

修复后使用公开 prepare + compile 工具再做真实 Windows 验证：

| 案例 | 结果 | 新 ARM 对象 SHA256 |
| --- | --- | --- |
| 单 DINT 两条赋值 | verified，2124 B | `d50133aa18f5a51a3bbeb11d20ee7ba1ddf8e1c6e14fc0f06b072397fdfcb3a7` |
| 两个 DINT 各赋值 | verified，2168 B | `417a5cabf2abd382c5b0eb1d3ab2ac14690ef61f7dc97791f4bf204362fcec1b` |
| BOOL/INT/DINT/REAL 四变量 + IF | verified，2752 B | `f23dcf8f2e0d2a69be37964e12d4218b8bcfad5f69e4c79034ea3a0a3517bbef` |
| 256 个 DINT 声明、首末变量赋值 | verified，2168 B | `60c4943e32e356f448552529af3f611542a6eae44ec7e6cea85f02e3bf23e066` |
| 错误语法 `COUNTER := ;` | failed，done=1/success=0 | 无有效对象 |

结果与输入/产物哈希见[本轮证据](11-plain-main-evidence-20261018.json)。完整工程前处理仍缺变量地址/保持区、库与 FB/FC、任务/中断、机型规则和软元件绑定；完整程序链接、封装、下载与真机运行验证均未由本轮对象实验完成。原 `CSTCompiler::StartSTPretreatCompile` 路径依赖工程数据中心和指令查询器的事实没有改变。
