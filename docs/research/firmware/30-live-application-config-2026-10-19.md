# 原生读回配置对照：压缩变换、opaque头差异与live-derived候选

2026-10-19。本轮只离线读取统筹新保存的17项实际设备文件及其只读报告，对照研究28固定模板，并静态读取既有AutoShop组件；没有执行Windows、操作设备、调用Git或修改历史候选。新增 [inspect_easy521_live_application.py](../../../tools/inspect_easy521_live_application.py)、[合成测试](../../../tools/tests/test_inspect_easy521_live_application.py)和[逐项元数据](30-live-application-config-2026-10-19.json)。二进制、原始符号文件及完整反汇编不提交仓库。

**12个实际配置的全部payload和模块DWORD均与固定模板逐字一致，文件哈希差异仅来自同一个不透明头字段的两字节。userprog与varsinfo的较短文件则是已恢复的原厂zlib变换，解压后的指令、入口索引和变量分配均与合法模板相同。** 当前Linux zlib重组还逐字复现了本设备215／173字节文件，因此这两项长度差异不是任务或分配内容不一致。

本轮另外准备两套17项live-derived候选：15项非ARM文件逐字来自同台设备读回，仅替换两个新Linux ARM对象。源MD5元数据的继承策略、工程归档写入策略、物理固件接受和新ARM运行仍未验证；`downloadable=false`。这些候选不替代或改写研究28的18项候选。

## 1. 只读证据范围

输入为统筹保存的 `.plcidf/native-execution-backup-zlp-20261019T072754Z/`：两个ARM对象、`symbol.sym`、`userprog.foid`、`varsinfo.foid`和12个配置；每项与同目录唯一的 `*-application-<name>.json` 核对成功状态、读操作范围、长度、SHA-256及相同target。报告仅复制来源路径、哈希和局部结构，不打印完整载荷。

这17份报告都指向 `001:002 / 0471:a531 / bcdDevice=0x0310`，序列号和CPU永久身份unknown。它们是多次非原子读取，本工具不把同一USB位置说成密码学设备身份，也不宣称原子完整备份。`project.hcpp` 的此前错误132未重试：它不能独自证明文件不存在、归档可省略或下载必须创建归档。本轮不发送或生成该项的传输决策。

## 2. 12个配置完全相同的部分

| 配置 | 文件字节数 | 偏移64后的payload字节数 |
|---|---:|---:|
| syscfg.foid | 789 | 725 |
| ethercat.foid | 270 | 206 |
| modbus.foid | 73 | 9 |
| canlink.foid | 72 | 8 |
| modcfg.foid | 579 | 515 |
| ethip.foid | 301 | 237 |
| EIPIns.foid | 88 | 24 |
| ecttrans.foid | 73 | 9 |
| dataLog.foid | 261 | 197 |
| OpcUAServer.foid | 97 | 33 |
| sftpserver.foid | 146 | 82 |
| Ecam.foid | 118 | 54 |

所有12对均满足：文件长度相同、`[64:]`逐字相同、`[60:64]`的调用者/模块DWORD相同。差异恰好只有文件偏移26、27：模板为 `e5 6f`，设备读回为 `ad 74`。这两个位置是原32字节opaque字段内的偏移6、7，不是payload或版本字段。

研究22已把该字段的来源追到空ACP字符串缓冲区的固定32字节复制，但没有证明NUL之后的字节是规范padding、设备必然忽略或可安全清零。因此本轮**逐字继承本台读回的完整配置文件与头**，不“修正”这两个字节，也不把相同payload提升为任意型号/固件的配置兼容证明。

## 3. userprog的两段压缩与长度表

原合法模板294字节，设备215字节。原64字节头和末尾项目UUID均相同；设备布局为：

| tag | 设备payload字节数 | 有界解压结果 |
|---|---:|---|
| `0x40000` | 34 | 56字节，逐字等于 `Output.prg` |
| `0x40100` | 23 | 112字节，逐字等于 `ILIndex.idx[30:]` |
| `0x40200` | 24 | 六个LE DWORD：`34,56,23,112,0,0` |
| `0x280` | 38 | 与模板及varsinfo相同的UUID |

长度表是三组 `(compressed_bytes, raw_bytes)`。本次第三段不存在，最后两值为零；工具不假装支持未取得样本的 `0x40120` 第三段。

静态函数 `AutoShop.exe!0xe70570` 先读取原64字节头及 `0x40000` 的tag/length共72字节，对实际payload调用压缩，将新长度写入原记录头；随后压缩 `0x40100`，可选处理 `0x40120`，再于 `0xe70a37` 写 `0x40200`，按顺序写三组压缩前后长度，最后复制余下UUID记录。它不重写opaque头，也不把ARM对象塞进userprog。

工具 `compress_existing_userprog` 对这一个明确的两段profile复现上述过程。Linux zlib runtime1.3默认压缩（level6）生成的整215字节文件与本次真实读回**逐字一致**。这只校准本次输入和输出，不承诺其他zlib版本生成相同压缩字节。

## 4. varsinfo的长度前缀和tag变换

原模板423字节，设备173字节。开头UUID、其后原64字节头以及末两条标志记录均保持相同；变量分配记录由 `0x30000` 改为 `0x30001`。设备新payload共34字节：

```text
LE32 raw_length=284
LE32 compressed_length=26
zlib stream，恰26字节
```

有界展开后284字节逐字等于合法 `VarsInfo.vi`，SHA-256为 `9c7cab9b340bbc5e5a352caa6fa54c3c8514b0c259b9691908995ca48ea1df95`。函数 `0xe70260` 在 `0xe70396` 设置tag `0x30001`，将新payload长度设为压缩长度+8，在 `0xe70402…0xe70432` 顺序写原长度、压缩长度、压缩数据，再复制原尾部。

工具 `compress_existing_varsinfo` 同样逐字复现了本设备173字节结果。它复用已有分配，未生成新变量、初值、保持区或内存地址。

## 5. 实际压缩库与版本条件

AutoShop调用桩 `0x17fb544` 对应IAT `0x1eb3e24`，导入 `zlibwapi.dll` ordinal2。独立读取该DLL导出表确认：ordinal2=`compress`，ordinal46=`compressBound`，ordinal26=`uncompress`。该DLL SHA-256为 `21582b3a68e8753322a1b1c7e550ae7fd305de4935de68fbde9f87570f484d00`；没有执行DLL。

当前正常文件表路径，在 `0xe7a75c` 调用 `0xe70170` 后才对userprog调用 `0xe70570`；varsinfo则在 `0xe7b269` 调用同一gate，再于 `0xe7b2ae` 调用 `0xe70260`。gate从PLCInfo对象偏移 `+0x64` 的四字节构造一个raw DWORD，执行无符号 `>=0x02030006` 比较，并在对象 `+0x168` 缓存启用值。

这恢复了原厂确实存在的条件变换，但本工具不擅自把raw阈值字段等同USB bcdDevice、CPU型号或已独立解码的语义版本。当前设备实际文件采用了这两种压缩形态，本轮保留其字节；没有自动根据未知版本去选择下载动作。所有静态代码范围哈希写入JSON，地址是研究22固定AutoShop哈希的首选PE VA。

## 6. 新候选的继承边界

最终稳定私有目录为：

- `.plcidf/research-30-live-config-20261019/candidate-reviewed-plus1/`
- `.plcidf/research-30-live-config-20261019/candidate-reviewed-plus2/`

每目录含17项 `files/`、研究28父manifest副本和 `live-application-candidate.json`。两项新ARM来自对应研究28候选；其余15项全部与本台backup逐字相同，包括压缩后的userprog/varsinfo、12份完整配置和原 `symbol.sym`。源码与新对象关联沿用父manifest，另记录本设备每项读回文件及报告的哈希。

`symbol.sym` 含旧程序元数据。研究29已确认其中厂商MD5来自有序编译文本集，完整预处理尚未复现；Simulator消费链按id/名称映射入口，但不能据此断言物理固件是否校验MD5。因此最终manifest明确 `symbol_metadata_inherited_from_device=true`、`symbol_vendor_md5_for_new_source_verified=false`、`symbol_metadata_policy_review_required=true`；既不假装元数据已重建，也不把重新生成它说成已由设备拒绝证明的必需条件。是否允许受控实测，由统筹结合独立ABI证据决定。

早期 `candidate-plus1/`、`candidate-final-plus1/2/` 保留原样，其中 `symbol_regeneration_required` 是早期审查策略，不是固件必须校验的证据；本报告的新manifest用更准确的未验证策略表述取代它。所有目录均未下装。

## 7. 有界工具与验证

```bash
.venv/bin/python tools/inspect_easy521_live_application.py \
  --candidate-directory .plcidf/research-28-fixed-config-20261019/candidate-final-plus1 \
  --live-directory .plcidf/native-execution-backup-zlp-20261019T072754Z \
  --report .plcidf/new-live-comparison.json

# 可选 --output-directory .plcidf/new-private-live-candidate
# 只准备本地候选，不执行设备操作。

.venv/bin/python -m unittest discover -s tools/tests \
  -p test_inspect_easy521_live_application.py -v
```

输入单文件限8MiB、累计读取64MiB，单段展开限4MiB。解压仅调用一次 `decompress(data, expected+1)`，要求实际长度精确相符、EOF已到、无unused_data/unconsumed_tail；不调用无界flush。长度表、tag顺序、EOF、UUID/头/尾关系均严格检查。

16项合成离线测试通过，覆盖截断/校验错误/尾随与串接流、声明长度错误/解压炸弹、第三段拒绝、输入类型/预算、配置实际payload或版本变化、读回哈希/target不一致、重复报告、父候选漂移、symlink/FIFO、输出覆盖/重叠和写入后输入漂移。候选与报告只能新建，失败证据保留；工具不具备设备API。测试与本次真实读回对照是不同证据层，不将软件验证计为真机新代码执行。
