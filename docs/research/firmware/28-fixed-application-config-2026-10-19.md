# 固定接口完整文件候选：userprog重组与无源码归档标记

2026-10-19。本轮只在 Linux 离线读取已有用户工程、研究22冻结组件和研究27新编译产物，没有执行 Windows 程序、访问 USB/网络设备或写入 PLC。交付 [prepare_easy521_application.py](../../../tools/prepare_easy521_application.py)、[15项合成测试](../../../tools/tests/test_prepare_easy521_application.py)及[逐项元数据](28-fixed-application-config-2026-10-19.json)。厂商文件仍在忽略目录，未提交二进制或完整反汇编。

**已准备两套有序18文件候选：新的 Linux MAIN→ARM→两级链接→已有全局绑定产物，配合明确固定的合法既有声明、指令入口和配置模板。** 两套只改变 +1.0／+2.0 规则，18个传输文件中恰好只有 `stprog.o`、`stuserprog.o` 两项哈希变化。`userprog.foid` 已从固定源文件精确重组；源码恢复项使用厂商支持的 `Empty` 标记，没有把旧 `.hcpp` 的 MAIN 正文伪装为新源码。

这是一套可审查的**完整文件集合候选**，不是已经通过固件兼容、实际任务调用、下载接受或 ARM 执行验收的应用。`candidate_file_set_complete=true` 只表示本轮明确选定18项全部存在且经过哈希核对；`downloadable/runtime_abi_verified/device_accepted/device_executed` 仍为 false。

## 1. 固定工程接口和来源

profile 为 `easy521-bess-existing-interface-v1`，只替换原 `MAIN` 入口逻辑，保留 `SBR_001`、已分配全局变量、初始化/任务配置和原始配置字节。研究27只使用既有 `g_rBESS_Pcmd:REAL`，ELF名 `G_RBESS_PCMD`，原有分配地址 `0x021000f0`；没有新建 DINT 地址或修改声明。变量地址的厂商含义不能外推为普通字节地址或 Modbus 寄存器。

模板冻结于 `.plcidf/research-28-fixed-config-20261019/template/`：研究22的15个程序/配置缓存及 `Output.sdt`，加同工程根目录的 `Output.prg`、`VarsInfo.vi`、`ILIndex.idx`、`Config.sdt`、`变量表.gvt`、`VarNameAndAddr.csv`。每项路径、大小、SHA-256在JSON中；工具逐项硬性校验22项固定哈希，不根据同名文件自动选取 `PLC/` 或 `OnlineVersion/` 的另一版本。

另外只读解析了研究22冻结的 `BESS_E521_DEV.hcpp`（SHA-256 `42d6b319152f9cdd15abb4ac50a5ebb734c139058af79e9997479578371659a2`）：偏移36的 zlib 流展开83,017字节，含54条 `[LE总长度, LE名称长度, 名称, LE文件长度, 文件]` 记录，末尾4字节未解释。54条内容逐字匹配当前同一用户工程对应源文件，其中一个同名扩展卡配置记录重复。此对照确认了归档中的声明与源配置，**不能确认所有缓存属于同次原厂构建，也不能把此旧归档绑定到新MAIN**。

保留属性和任务周期暂未从其二进制字段解释出来；工具保存原值与 unknown。原工程初始化配置不因为新规则需要方便而改写为零。设备准确固件版本和条件功能选择，等待统筹另行核对。

## 2. userprog和变量文件的具体结构

`userprog.foid` 的294字节严格由以下6条 little-endian DWORD tag/length记录组成：

| 文件偏移 | tag | payload长度 | 已验证来源 |
|---:|---:|---:|---|
| 0 | `0x100` | 4 | `foid` marker |
| 12 | `0x200` | 32 | 原头不透明元数据，原样保留 |
| 52 | `0x201` | 4 | 原头调用者字段，原样保留 |
| 64 | `0x40000` | 56 | 逐字等于 `Output.prg` |
| 128 | `0x40100` | 112 | 逐字等于 `ILIndex.idx[30:]` |
| 248 | `0x280` | 38 | 项目UUID，与变量文件一致 |

`ILIndex.idx` 的长度142字节：LE DWORD magic `0x989681`、WORD版本1、20字节零值构成26字节头，偏移26是 DWORD count=28，偏移30开始为28个 DWORD。此样本索引16、19、24分别存0、1、2，其余为 `0xffffffff`。静态函数 `0x169d2e0` 从字面量 `\ILIndex.idx` 读取26字节头、count和数组；函数 `0x169db10` 在 `0x169df4e` 写tag `0x40100`，在 `0x169df6c` 将count乘4作为字节长度。地址均为研究22固定哈希 `AutoShop.exe` 的首选PE VA。

工具用**原64字节头** + 新拼接的三个明确tag/length/value重组整个文件，结果逐字等于合法冻结模板；没有把32字节字段猜成零。这里的 `Output.prg` 是已有指令载荷，ARM MAIN在独立ELF中，不能把56字节文件称为完整机器码。

`varsinfo.foid` 共423字节，顺序为 `0x28000/38`、`0x100/4`、`0x200/32`、`0x201/4`、`0x30000/284`、`0x3000f/1`、`0x30020/4`。`0x30000` 载荷逐字等于根目录 `VarsInfo.vi`，首条UUID与userprog末条UUID相同；末两条含义未确认，完整继承。`symbol.sym` 为两个 `bmys` 段，每段均有id1的 `MAIN`、id2的 `SBR_001` 名称记录；其未知头字段原样保留，未将16字节字段擅自解释为程序MD5。

目前仍没有证明上述指令索引在运行时如何调到MAIN、初始化何时执行或任务周期是多少。符号存在、数组相同与实际扫描执行是不同层证据，后者必须真机验收。

## 3. 厂商正式支持的无源码归档路径

不能直接拿旧 `BESS_E521_DEV.hcpp` 当新逻辑的 `project.hcpp`。本轮恢复了更小且不混淆来源的原厂路径：

1. `0xe71ce0` 在 `0xe71d12` 检查参数 `[ebp+0x0c]`；为0时进入 `0xe71f69` 分支。
2. 该分支使用字面量 `0x1f595e0`：`\Temp\Empty.hcpp`，创建文件。
3. `0xe71fe2` 通过 `HString(char const*)` 读取 `0x1f595d8` 的ASCII常量 `Empty`，查询字符串长度后在 `0xe72008` 写入，恰好5字节，不含NUL。
4. 检查器 `0xe752f0` 在 `0xe753d7` 专门判断文件长度5，再在 `0xe75429` 与 `0x1f596c8` 的Unicode `Empty` 比较；相等被接受。

因此工具为候选 `project.hcpp` 生成 `b"Empty"`，不是伪造压缩工程、填零或漏掉列表项。新 ST、IR和构建来源保存在Linux端，设备端此项不能用来恢复源工程。参数的具体设置项UI名称本轮未恢复，不把它擅自命名为某个复选框；也未单独证明设备接受这条原厂路径。

## 4. 有序文件集合与真实候选

顺序沿用研究16固定调用表：`project.hcpp → userprog.foid → stprog.o → stuserprog.o → symbol.sym → varsinfo.foid`，再依次 `syscfg.foid、ethercat.foid、modbus.foid、canlink.foid、modcfg.foid、ethip.foid、EIPIns.foid、ecttrans.foid、dataLog.foid、OpcUAServer.foid、sftpserver.foid、Ecam.foid`。本轮不添入缺失的 `webserver.foid` 或其他型号的 `autosys.cfg`。各配置完整继承已有封装，既不把原 `.dat` 简单加头，也不重算未知元数据。

| 变体 | 稳定私有候选 | `stprog.o` SHA-256 |
|---|---|---|
| 每次MAIN调用 +1.0 | `.plcidf/research-28-fixed-config-20261019/candidate-final-plus1/` | `318040ea24813ea691a6d7f17129a30fbbb1aa83bef3a30340d3ade2e370fb90` |
| 每次MAIN调用 +2.0 | `.plcidf/research-28-fixed-config-20261019/candidate-final-plus2/` | `314ecb7828b691975b90814aa8ab7d04ce16c3236d0f0f5b0b83a09841c15332` |

每目录包含 `files/` 的18项、`inputs/` 的只读输入快照和 `application-candidate.json`。旧 `candidate-plus1/`、`candidate-plus2/` 和 `candidate-plus*-v2/` 保留原样；最终候选消费研究27的 `candidate-final-plus*` 报告及对象，不冒充早期工具结果。配置来源的原厂同批构建真实性仍unknown；本轮新候选的源码、对象和输入哈希有明确关联，但JSON关联不是密码学构建证明。

## 5. 工具验收与继续工作

```bash
.venv/bin/python tools/prepare_easy521_application.py \
  --template-dir .plcidf/research-28-fixed-config-20261019/template \
  --source .plcidf/research-27-native-bound-20261019/sources/plus1.st \
  --stuserprog .plcidf/research-27-native-bound-20261019/candidate-final-plus1/bound/stuserprog.o \
  --stprog .plcidf/research-27-native-bound-20261019/candidate-final-plus1/bound/stprog.o \
  --build-report .plcidf/research-27-native-bound-20261019/candidate-final-plus1/candidate-report.json \
  --output-dir .plcidf/new-fixed-candidate

.venv/bin/python -m unittest discover -s tools/tests \
  -p test_prepare_easy521_application.py -v
```

15项合成离线测试通过：严格TLV边界/顺序/EOF、UUID与载荷关联、配置任一字节漂移、源链或接口/输出哈希错误、重复与畸形报告、非ARM/缺入口、symlink/FIFO、已有输出不覆盖、输出嵌入模板拒绝、读取预算以及生成后输入漂移。无子进程或设备写入；失败目录不重试、不清空。

下一步已从“缺哪些包装字节”收敛到**对具体设备核对这18项的适用性、固定入口/初始化的真实行为以及完整事务结果**。统筹需要先保存真实设备文件和状态，再决定如何对照这套候选；本报告没有执行这些动作，也没有把读取成功提升为写入/运行成功。
