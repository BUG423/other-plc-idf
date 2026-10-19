# Easy521：MatIEC 源码到既有接口绑定候选（2026-10-19）

## 1. 本轮结果

已将实际 **MatIEC ST → C → Linux Clang ARM 对象**接入研究27的固定工程流水线，完成两级 LLD 链接及来自同工程 GVT 的 SInfo 绑定。两份含 `IF/ELSE`、阈值复位及不同增量的 ST 生成了不同的新 MAIN、生成 C 和两个绑定对象。没有调用 Windows、设备或厂商 ST 编译器。

这完成的是**受限、无 I/O 的内存实验候选**。`downloadable`、`complete_application`、`runtime_abi_verified`、`runtime_scheduling_verified` 均为 `false`。候选未在本记录中下装、运行，也没有实现厂商中断分发钩子。完整应用文件组装和真机验收由后续独立记录关联，不能以本次源码编译成功替代。

机器证据见 [33 JSON](33-matiec-bound-application-2026-10-19.json)。编译器实现、真实头文件依赖及 Linux 主机上的语义检查另见 [研究31](31-matiec-native-arm-2026-10-19.md)；既有地址来源见 [研究27](27-native-bound-application-2026-10-19.md)。本轮未修改27的历史报告或候选。

## 2. 显式后端及不变的接口约束

`tools/bind_native_application.py` 新增 `compiler_backend=matiec-clang`，默认仍为 `restricted-llvm`：

| 后端 | 编译输入与工具 | 源码证据 |
| --- | --- | --- |
| `restricted-llvm` | 原有限定 REAL 自增语法；需要 `llc` | 保留原 `ir_sha256` |
| `matiec-clang` | ST 正文交给真实 MatIEC，生成 C 交给 Clang；需要 `matiec_root`、`clang` | 原 ST、生成 C/头、adapter、实际依赖、manifest、工具各自哈希；没有伪造 IR 字段 |

两条后端都必须通过原12份工程文件、两份库的完整固定哈希校验；GVT与CSV的19个已分配变量逐条比对。本轮只允许声明 `g_rBESS_Pcmd:REAL`，既有地址 `0x021000f0`、32位宽和 SInfo 类型8仍从工程记录核验，未新增分配器或复制另一工程地址。

MatIEC后端允许的范围由其实际语法/类型编译与生成接口检查共同限定：单一外部 REAL、无局部保持状态、无辅助函数的赋值、普通表达式及 `IF/ELSE`。没有通过原简化 ST 正则“放行”未知语法。额外全局、未知 ST 名称、定时器、功能块、强制值、硬件 I/O 等不被此 profile 接受；完整 IEC 61131-3 支持不在此结论内。

SBR_001、两库和任务配置字节均为固定输入，只有 MAIN 本次从源码生成。两级链接顺序保持 `MAIN + SBR_001 + SysLib → stuserprog`，再 `stuserprog + STCLib → stprog`。每个绑定对象只修改选定全局的 `st_value/st_info`；所有其他字节保持其本次链接结果。

## 3. 实际规则与产物

两份私有源文件位于 `.plcidf/research-33-matiec-bound-20261019/sources/bounded-plus{1,2}.st`。注释注明：Pcmd只作诊断计数，不作工程功率命令；周期、保持、启动是否采用 GVT初值均未知。对从有限的 `[0,100]` 状态开始的实验，规则为：

```iecst
IF g_rBESS_Pcmd >= 100.0 THEN
    g_rBESS_Pcmd := 0.0;
ELSE
    g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0; (* 第二份为 2.0 *)
END_IF;
```

达到阈值的下一次调用复位；这不是毫秒计时，也不在入口重置 PLC 全局。负值或 NaN 起始状态没有恢复规则。无 STOP/故障回调或强制值支持，不能直接用于工艺控制。

| 产物 | +1 SHA-256 | +2 SHA-256 |
| --- | --- | --- |
| ST 正文 | `9850281518c71aca26298e8fa3f4912b4dc4f388b071e6e8419ff3f2eec545d7` | `6fb117828fdf755affbfb1fc8ad384f2854129f0896006fbf631756cdbf06102` |
| 新 MAIN.o | `421e94efdc684e601289c9bf1812a10d5f1f41e7d107def18fb9d3968cb238b0` | `ec026bbf0f1165f2f6a1ce2c1e673f1ec38dc72d3c151be0edf5c181973c3b28` |
| bound/stuserprog.o，4404 B | `b35394706136fdbdb7eef8bbb524918cba87d6c2db0f66905933fc3c255b78c7` | `57f810a38d7d81d63709efc215c1d928681bdcd7125166f92d9b1e679686da5a` |
| bound/stprog.o，143924 B | `0ad1d636e56dceb2b0d4e6c37af104a215f44b6698fa73310478a249545dc95e` | `83de0d638d6005e77374061942589bea67e2e029ed2951f44d7a6f5192fd937b` |

稳定聚合报告分别为：

- `.plcidf/research-33-matiec-bound-20261019/candidate-final-bounded-plus1/candidate-report.json`
- `.plcidf/research-33-matiec-bound-20261019/candidate-final-bounded-plus2/candidate-report.json`

每个目录内 `native-main/matiec-probe-report.json` 为实际编译报告，`linked/link-report.json` 为两级链接报告，`bound/` 为交给后续组装器的两个对象。聚合报告 schema仍为 `plcidf.native-bound-application.v1`，新增 `compiler_backend`；`source_chain.matiec_c` 分别保存生成 C、adapter、头、manifest与工具来源。`source_chain` 的源码、编译报告和链接报告哈希与两个对象关联。

新 MAIN 函数为56字节，只定义 `MAIN`、只引用 `G_RBESS_PCMD`，无外部 helper 调用。重定位仅有对应全局地址的 MOVW/MOVT 和 `.ARM.exidx` 的 PREL31；保留R9、Thumb-2、VFPv3和基础AAPCS属性均经实际对象检查。这是局部结构匹配，不是厂商完整 ABI 认证。

## 4. 复现命令

以下依赖本机已有的固定厂商工程、库和已安装 MatIEC/Clang；它不表示新克隆仓库自带这些私有输入。输出目录必须全新。

```bash
.venv/bin/python tools/bind_native_application.py \
  --compiler-backend matiec-clang \
  --source .plcidf/research-33-matiec-bound-20261019/sources/bounded-plus1.st \
  --globals-path .plcidf/research-33-matiec-bound-20261019/sources/globals.json \
  --project-dir /mnt/e/PLCProjects/PLCIDF_LAB_20261017/BESS_E521_DEV \
  --syslib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_SysLib.o \
  --stclib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_STCLib.o \
  --matiec-root /home/zss/.cache/plcidf/matiec \
  --clang /usr/bin/clang-20 \
  --lld .plcidf/research-tools/lld20/extracted/usr/lib/llvm-20/bin/ld.lld \
  --output-dir .plcidf/research-33-matiec-bound-20261019/new-review-candidate
```

没有 `--llc` 参数。工具每个子进程默认10秒、有界且不自动重试。MatIEC commit 为 `3a41303bcb4e50403417bc39c63f88981d7d961e`；编译器、Clang、LLD及adapter的实际SHA-256见JSON。

## 5. 验证及剩余缺口

三份相关测试共52项通过，使用本机实际 LLC/LLD/MatIEC/Clang。新增 binder 测试覆盖真实两份 `IF/ELSE` 源码→对象→两次链接→来自合成GVT的地址绑定、实际 MatIEC 拒绝未知名称后不调用链接器、编译后生成头被修改时拒绝候选，以及缺少工具/额外全局的预检失败。合成接口和库有明确的测试替换，不冒充厂商工程；上述两份私有候选则使用未替换的生产固定哈希。

编译器的 `-MD` 捕获29项实际依赖，包括 adapter、POUS.c/h、MatIEC头及Clang resource头；没有ARM libc。本工具在链接后重读生成文件、adapter、实际依赖和原工具/manifest，不能将编译后漂移仍标为关联成功。旧 LLVM路径的真实编译/绑定和输入漂移拒绝也通过。

本轮没有移植 `_HC_INTERRUPTFCBYST` 等厂商中断分发调用。固定工程目录虽然列有 `INT_001.ST`，它为233字节、与空 `SBR_001.ST` 逐字相同；STTemp只有 MAIN/SBR_001 对象，symbol双表也只记录这两者。尚未解明 ILIndex各槽与中断的完整对应，**不能据此断言所有中断已禁用**。配置原字节不变仅证明配置身份，不能证明新 MAIN保留了厂商的扫描/中断调度语义。不会猜测原型后插入调用。研究34将独立审查该接口。

后续必须分开验收完整配置/符号组装、目标身份、设备文件回读、CPU状态和实际运行规则；其中任何一步尚未完成都保留未知。此 profile 的局部成功也不外推到其他型号、定时器、FB、初始化、保持或物理输出。
