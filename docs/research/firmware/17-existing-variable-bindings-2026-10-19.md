# 从合法工程变量记录独立恢复 8 项全局符号绑定

2026-10-19。本轮只读工程文件和已有静态证据；没有访问 Windows、GUI、USB、网络设备或 PLC，没有执行厂商 EXE/DLL，没有修改 ELF 或原工程。原始文件的私有只读副本位于 `.plcidf/research-17-variables-20261019/inputs/`，仓库仅保留解析工具、合成测试和[元数据](17-existing-variable-bindings-2026-10-19.json)。[16 号链接实验](16-application-transaction-2026-10-19.md)保持冻结。

**可以不读取参考 ELF，从现有合法 `变量表.gvt` 独立得到这 8 个变量已分配的地址和声明类型。** 实际解析完整 19 条记录后，19 项地址/位宽与独立 `VarNameAndAddr.csv` 一致，19 项类型与工程导入声明一致；之后再打开参考 ELF，所选 8 项地址和普通全局符号类型均吻合。这个结果支持“固定已有全局/任务接口，复用合法分配”的下一阶段，不是新变量分配器，也未执行符号注入或证明可下装。

## 1. 解析输入与参照严格分开

| 文件 | 长度 | 本轮作用 |
|---|---:|---|
| `变量表.gvt` | 1,503 | 唯一地址/名称/类型解析输入，SHA-256 `63700c1f9b5a4b1201201442b1d614344d9b6346fd2ad913b2c22796560b95c5` |
| `VarNameAndAddr.csv` | 24,548 | 独立 UTF-16 地址/位宽导出，仅交叉验证 |
| 工程导入声明 CSV | 见 JSON | 交叉验证 BOOL/INT/REAL 声明，未提供地址 |
| `VarsInfo.vi` | 284 | 冻结并观察分配元数据，不用于计算这 8 个地址 |
| `ReAllocVarsInfo.vi` | 4 | 冻结并保留未知意义，不用于计算地址 |
| 根目录 `varsinfo.foid` | 423 | 保留生成容器身份，未当作已解码变量表 |
| 16 号冻结的 `Temp/stprog.o` | 127,464 | 解析结束后才打开，作为结果参照，绝不向解析器提供地址 |

工具不接受 ELF 输入。若 CSV 缺项或地址/位宽与 GVT 不符，返回拒绝并保留 GVT 解析值，不用 CSV 覆盖或补足字段。两个导出文件即使相互一致，也可能相对于源码/构建陈旧；未来注入器仍须绑定同一次合法构建的输入闭包。

## 2. GVT 的有界记录结构

当前 GVT 是可直接读取的 MFC 风格序列化，不需要解密变量名。文件开头为 `AutoShop`，归档版本字段为 `6.0`；这个版本号不等于 AutoShop 应用版本。前导 class-tree/LVT 结构的 **217 字节** SHA-256 为：

`551997c6cc365daf1275ee90349f48cfc2c5f2d05225732e0877fb435bfeb03f`

新增解析器只允许此明确前导配置。`0xd9` 处是 LE DWORD 变量计数 **19**，第一条从 `0xdd` 开始，之后按字段长度依次解析，不搜索名字或扫描疑似地址：

| 字段顺序 | 结构 | 验证约束 |
|---|---|---|
| 1、2 | 两个 LE DWORD 类型码 | 当前标量记录两者必须相同，只允许已核验的 1、4、32 |
| 3 | u8 字节数 + ASCII 名称 | 简单标识符，最长 63 字符，大小写不敏感唯一 |
| 4 | u8 字节数 + ASCII 初值文本 | 当前为 `OFF` 或 `0`，按真实长度读取 |
| 5 | 四个 flag 字节 | 仅接受观察到的 `01 01 00 00`，不猜各 flag 意义 |
| 6 | LE DWORD 已分配地址 | 从本字段直接读取；不是按 CSV 或 ELF 推导 |
| 7 | u8 字节数 + ASCII 注释 | 按长度读取 |
| 8 | 12 字节尾部 | 空 Unicode CString 标志 `ff fe ff 00`，再两个零 DWORD |

必须恰好消费计数指定的记录并到达 EOF。未知前导/版本、扩展或 Unicode 字符串、数组/实例/命名空间、未知类型/flag、重复名称/地址、截断与尾随数据均拒绝。文件上限 1 MiB、记录上限 256。

当前合法标量类型码与独立类型声明/CSV 位宽的对应是 `1 → BOOL/1 bit`、`4 → INT/16 bits`、`32 → REAL/32 bits`。这是本轮支持范围，不把其他枚举值臆测为 DINT、数组或 FB。内部地址暂保持不透明值；不能直接换算为主机指针、Modbus 寄存器或物理 I/O。

## 3. 8 个绑定确实来自 GVT 字段

以下偏移均为 GVT 文件绝对偏移；地址来自第 6 字段，声明类型来自第 1/2 字段。名称大小写保持归档原貌。

| GVT 名称 | 类型码/声明 | 记录起点 | 地址字段偏移 | 独立读取的既有地址 |
|---|---|---|---|---|
| `g_iBESS_Mode` | 4 / INT | `0x401` | `0x41c` | `0x2100130` |
| `g_rBESS_Confidence` | 32 / REAL | `0x550` | `0x571` | `0x21001c0` |
| `g_rBESS_InfluenceP` | 32 / REAL | `0x4c4` | `0x4e5` | `0x2100180` |
| `g_rBESS_InfluenceQ` | 32 / REAL | `0x50a` | `0x52b` | `0x21001a0` |
| `g_rBESS_Pcmd` | 32 / REAL | `0x379` | `0x394` | `0x21000f0` |
| `g_rBESS_Qcmd` | 32 / REAL | `0x3bc` | `0x3d7` | `0x2100110` |
| `g_rBESS_SensVP` | 32 / REAL | `0x43c` | `0x459` | `0x2100140` |
| `g_rBESS_SensVQ` | 32 / REAL | `0x480` | `0x49d` | `0x2100160` |

完整 19 条的字段偏移、输入哈希、CSV 行号和参照检查均在 JSON。解析器可以选择任意受支持的已有变量，不硬编码这 8 个名字或地址。其边界是前导序列化配置、简单 ASCII 标量和已分配地址范围；不是只对文件名执行 grep 的脚本。

## 4. SInfo 的类型 8 与标量类型码是不同概念

16 号报告已经定位 `H530Converter.dll` 的普通全局变量 `SInfo` 构造逻辑：每项 12 字节，名称指针 `+0`、地址 DWORD `+4`、类别 BYTE `+8`；`0x1016dacc` 对这个类别写入常量 **8**。因此不能把 GVT 的 `INT=4` 或 `REAL=32` 直接塞进 ELF `st_info`。

地址解析 helper `0x1016d090` 从 `IH530SysResMgr::GetVarByName` 取得工程变量对象，经其地址/类型访问器返回已有分配。GVT 的显式地址字段与 8 项最终值一致，为“该访问器读取项目分配”的链条提供独立文件证据；完整变量对象构造和分配算法仍未复现。

名称经过 `::` 前缀处理和 `CSTCompiler` 虚表 `+0x3c` 的映射。本轮 8 个普通 ASCII 全局名转大写后，恰好对应参考 `G_IBESS_MODE/G_RBESS_*`；工具将其标为 `compiler_symbol_candidate`。没有把这个局部结果推广到 Unicode、别名、命名空间、实例或用户函数。

将独立解析出的地址和候选类别与参考 ELF 对照，8 项均为：`st_info=(0x10 & 0xf0)|8=0x18`、`st_value=GVT 已分配地址`、`st_shndx=0`。这与 `STRelocation.dll` 的 `0x10003d09…0x10003d1c` 更新规则一致。本轮**只计算/比较候选，不修改任何 ELF 字节**。

## 5. 工具、实际结果与负向测试

新增 [inspect_vendor_variables.py](../../../tools/inspect_vendor_variables.py)：

```python
parse_gvt(data: bytes) -> dict
parse_address_csv(data: bytes) -> dict
inspect_variables(gvt_path, *, address_csv=None, required_names=None) -> dict
```

文件只读、无子进程、无厂商动态调用。结果的 `ok=true,status=existing-records-inspected` 只表示本地记录成功解析及可选 CSV 一致；始终 `allocator_implemented=false,elf_patched=false,downloadable=false`。

```bash
.venv/bin/python tools/inspect_vendor_variables.py \
  --gvt /mnt/e/PLCProjects/PLCIDF_LAB_20261017/BESS_E521_DEV/变量表.gvt \
  --address-csv /mnt/e/PLCProjects/PLCIDF_LAB_20261017/BESS_E521_DEV/VarNameAndAddr.csv \
  --name g_iBESS_Mode --name g_rBESS_Confidence
```

实际执行结果：19 条 GVT 完整解析、19 项 CSV 地址/位宽通过、19 项导入声明类型通过，后续 8 项参考 ELF 对照通过；原输入 SHA-256 复核未变。地址 CSV 是带 BOM 的 UTF-16 制表符导出，系统变量注释中实际含未转义制表符，所以工具读取首两列和最后的位宽列；不把注释内额外单元误认为地址字段。

新增 [test_vendor_variables.py](../../../tools/tests/test_vendor_variables.py) **10 项测试通过**：完整记录及偏移、所有长度的截断、计数/EOF 不一致、未知前导/类型、大小写重复名称、重复/未分配地址、未知 flag/尾部/Unicode 长度、CSV 缺 BOM/重复/错误位宽、CSV 冲突不覆盖 GVT、缺少要求变量、symlink 拒绝。测试使用合成前导与记录，不内嵌厂商 GVT；实际前导的正向验证由上述私有真实文件实验承担。

## 6. 下一步可以做什么、仍缺什么

可以在**固定已有合法全局/任务接口**的前提下，将本工具产生的记录、GVT/源码/构建哈希交给下一批受限 `SInfo` 注入器。那个注入器应先验证原 ELF 的目标 UND 名称、原类型/值和节索引，保留原产物并输出到新目录；再逐项比较完整产物，不能以候选映射相同直接放行设备写入。

不能据此新增变量或改变保持属性：`VarsInfo.vi` 中虽可见两个初始范围/数量形态的记录，`ReAllocVarsInfo.vi` 当前只有四个零字节，但分配器、保持区、任务/FB 内存、数组、位地址单位和完整名称转换仍未解码。生成的 `varsinfo.foid` 也没有被当作已解析 ABI。新变量的地址必须由独立验证的分配算法产生，不能复制 GVT/参考 ELF 地址冒充新分配。

当前收获是已有合法分配到 8 项候选符号绑定的可重复离线证据。完整 ST 编译、全部配置/任务包、独立设备事务和执行观察仍需各自验收。
