# Linux LLD 对现有 ARM 对象的可重定位链接实验

日期：2026-10-17。范围：在 WSL/Linux 上，用官方 Ubuntu LLD 对已有厂商编译产物做 `-r` 部分链接。**Linux 可以处理这批现有 ARM ELF 对象，但本次没有实现 ST 编译、厂商应用打包、下载或运行。**

## 实验边界与证据

全部执行文件是 Linux 工具；没有启动 Windows 进程、厂商 EXE/DLL、USB 或 PLC 会话。厂商输入只读，先复制到 `/tmp/plcidf-native-link-20261017/inputs`，核对复制前、复制后及副本 SHA-256 一致。下载、解包和输出均留在该临时目录，没有 sudo 或系统安装。

可审查证据为 [独立元数据 JSON](20261017-linux-link-experiment-metadata.json)：包版本与 APT 哈希、工具哈希、每个输入/输出的 SHA-256、命令及退出码、ELF 头/属性、重定位统计、未解析符号与库名称交集。原始完整符号表、完整重定位表和二进制只留在本机 `/tmp`，不随仓库提交。元数据中的未解析统计仅计非空名称的 GLOBAL/WEAK 符号，并同时区分条目数与唯一名称数；本次二者相等。

## 工具与冻结输入

使用现有 Ubuntu noble-updates 软件包索引，从 `archive.ubuntu.com/ubuntu` 下载以下包；未执行 `apt update`。下载文件 SHA-256 均与 APT 索引中的 SHA256 一致。

| 官方包 | 固定版本 | 下载字节数 | SHA-256 |
|---|---|---:|---|
| lld-18 | `1:18.1.3-1ubuntu1` | 1,371,582 | `c574594d714bf445f4c2323fa99374c05b79be09a3899edbf9d4b7265c9ccc57` |
| libllvm18 | `1:18.1.3-1ubuntu1` | 27,518,500 | `af6197096ce3b89aab86de59f01c1b5feb008b2ae1514a03906c0664edb8b0b1` |

包的原始下载路径见 JSON 的 `packages[].apt_metadata.Filename`。本次 linker 版本为 `Ubuntu LLD 18.1.3 (compatible with GNU linkers)`；解包后的 `usr/lib/llvm-18/bin/ld.lld` SHA-256 为 `7ad9a0e8fe6d0e79b71172d731e33872c0274e49fceb7b516d774876d5a58ade`。检查工具为 GNU readelf/nm 2.42，其二进制哈希也已记录。LLD 通过自身相对库搜索路径加载同目录树解包的 LLVM 库；未替换系统库。

工作工程为 `E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV`，使用实际活动嵌套目录中的对象；与仓库旧归档的对象哈希不同。

| 冻结输入 | 字节数 | 未解析条目／唯一名称 | 重定位条目 | SHA-256 |
|---|---:|---:|---:|---|
| `STTemp/MAIN.o` | 4,472 | 36／36 | 84 | `391ac85bffa6f5399c02fbe537bf98a2e32748c9c09cf0ec58936ea4b33643db` |
| `Temp/stprog.o` | 159,012 | 82／82 | 5,497 | `b95099a749ed9c96c4a7d16a8b391a6a0c87d7e63ea614b34ff6af123f33f8c6` |

另冻结 AutoShop `Bin/SysLib/ARM` 与 `Bin/SysLib_New/ARM` 下各三个对象：`_STCLib.o`、`_SysLib.o`、`_HC_EMPTYFUC.o`。新旧 `_SysLib.o` 同为 2,808 字节、哈希相同；`_STCLib.o` 分别为 120,756 与 122,944 字节，哈希不同。全部具体路径、哈希和各库符号统计见 JSON，不根据目录名推断哪一套适用于某个固件。

## 结果

每条链接命令均为 `ld.lld -r -o <output.o> <inputs...>`，没有最终地址布局、入口地址、runtime 导入表或厂商包装步骤。

| 组别 | 输入 | 返回码 | 输出字节数 | 输出未解析唯一名称 | 输出重定位条目 |
|---|---|---:|---:|---:|---:|
| A | MAIN | 0 | 4,600 | 36 | 84 |
| B | 厂商 stprog | 0 | 178,536 | 82 | 5,497 |
| C | MAIN＋旧 ARM 三库 | 1 | 无 | — | — |
| D | MAIN＋新 ARM 三库 | 1 | 无 | — | — |
| E | MAIN＋旧 `_STCLib`＋`_SysLib` | 0 | 143,824 | 46 | 3,778 |
| F | MAIN＋新 `_STCLib`＋`_SysLib` | 0 | 146,012 | 46 | 3,903 |

C、D 均明确报 `duplicate symbol: _HC_EMPTYFUC`：同套 `_SysLib.o` 和 `_HC_EMPTYFUC.o` 都定义该名称。没有用忽略重复定义选项绕过错误。E、F 是针对该单一问题的有界后续对照，仅去掉独立占位对象 `_HC_EMPTYFUC.o`；不代表已经恢复厂商真实的对象选择规则。

A、B、E、F 的输出都是 ELF32、小端、ARM、ET_REL、EABI5，无程序头、入口地址为 0；保留 ARMv7 Application、Thumb-2、VFPv3、NEONv1 等属性。保留了 `R_ARM_ABS32`、`R_ARM_NONE`、`R_ARM_PREL31`、`R_ARM_THM_MOVT_ABS` 和 `R_ARM_THM_MOVW_ABS_NC` 重定位；各类型计数见 JSON。**重定位仍存在是部分链接的正常结果，不是已经完成 runtime 重定位的证据。** ELF 的 `UNIX - System V` 字段也不能证明 PLC 运行 Linux。

E 输出 SHA-256 为 `2b772ddbd67569369d2bcc93e13eece6704c54476be22da388553832d9d596ce`；以相同输入、相同本机工具再次执行，输出哈希一致。该检查仅证明这一次本地重复的确定性，未验证干净机器或不同 LLD 版本。F 输出 SHA-256 为 `7991fb6276005ee40d7a0b851ae066f2da95b60246c369e242715971098ed47f`。

## 对象依赖与可替换层判断

按 GLOBAL/WEAK 定义名称比较，MAIN 的 36 个未解析名称中，三库只能提供两个：`_HC_EMPTYFUC`、`_ST_INT64_ADD`，均由 `_SysLib.o` 提供；独立 `_HC_EMPTYFUC.o` 也提供前者。`_STCLib.o` 本身不直接提供 MAIN 的未解析名称。

E、F 确实从 MAIN 的未解析集合中移除了这两个名称，但加入整份库对象又引入 12 个新依赖，所以结果为 `36 − 2 + 12 = 46`，不是完整闭合。新依赖涉及 `_HC_WATCHDOG`、`_HC_LD_ERROR_ADD`、位复制、移位、舍入等厂商运行时功能。MAIN 本身还依赖全局变量地址、功能块调用/实例状态、设备位读写以及 ARM 展开支持。JSON 保留准确名称集合。

厂商 stprog 的 82 个未解析名称与这些三库的定义名称交集为 0；不能凭此推断厂商没有使用这些库，已部分链接的对象可能已经合入其定义。E、F 与厂商 stprog 的文件哈希、已定义名称集合、未解析名称集合均不同；本实验没有纳入完整工程的其他 POU/功能块对象和厂商链接规则，因此不将它们视为厂商 stprog 的替代品。名称相同也不证明参数布局、调用约定或版本 ABI 兼容。

结论限定在一个可替换子层：**Linux 开源链接器能接收本次已有 ARM 目标文件并做部分链接。** 要移除 Windows，仍需独立解决 LiteST 前端与代码生成、所有 POU/变量/任务 ABI、实际对象与库选择、runtime 导入/重定位、程序包装与一致性校验、下载协议及真机运行。`-r` 默认允许未解析符号，退出 0 不代表完整构建，更不代表可以写入 PLC。

## 最小复现命令

以下命令仅下载、解包和操作 Linux 文件。应先核对源输入 SHA-256 与本报告一致；源文件后续重新编译后不同，则属于另一组实验。

```bash
mkdir -p /tmp/plcidf-native-link-20261017
cd /tmp/plcidf-native-link-20261017
apt-get download lld-18=1:18.1.3-1ubuntu1 libllvm18=1:18.1.3-1ubuntu1
sha256sum lld-18_1%3a18.1.3-1ubuntu1_amd64.deb libllvm18_1%3a18.1.3-1ubuntu1_amd64.deb
dpkg-deb -x lld-18_1%3a18.1.3-1ubuntu1_amd64.deb root
dpkg-deb -x libllvm18_1%3a18.1.3-1ubuntu1_amd64.deb root
root/usr/lib/llvm-18/bin/ld.lld --version
```

实际执行前，将两份工程对象和新旧六份库对象复制到 `inputs`，并逐个对照元数据的 `inputs.*.sha256`。复制后的文件布局与每条完整 argv 均见 JSON；例如成功组 E：

```bash
mkdir -p outputs
root/usr/lib/llvm-18/bin/ld.lld -r -o outputs/E-main-old-syslib-no-duplicate.o \
  inputs/main.o inputs/old/_STCLib.o inputs/old/_SysLib.o
readelf -hW outputs/E-main-old-syslib-no-duplicate.o
readelf -AW outputs/E-main-old-syslib-no-duplicate.o
readelf -rW outputs/E-main-old-syslib-no-duplicate.o
readelf -sW outputs/E-main-old-syslib-no-duplicate.o
sha256sum outputs/E-main-old-syslib-no-duplicate.o
```

保持 `-r` 和对象顺序不变；不要为得到退出 0 添加忽略重复定义选项，也不要把输出写回原厂工作工程。输出二进制本轮仅存于 `/tmp`。
