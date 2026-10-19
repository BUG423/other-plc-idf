# Linux 中的既有变量绑定、真实本地闭环与当前缺口

2026-10-19。用户确认设备是 Easy521，并要求继续尝试纯 Linux 编译、代码操作与监控。本轮只在 Linux 中操作本地文件、编译器和临时运行进程，没有调用 Windows 程序、设备协议、PLC 下载、CPU 控制或程序清除。证据目录为 [easy521-linux-wiring-20261019T045046Z](../../../evidence/easy521-linux-wiring-20261019T045046Z/)。

## 两种目标分别验收

| 目标 | 本轮实际结果 | 边界 |
|---|---|---|
| Linux 电脑上的 ST 运行时 | 真实编译、本地部署、扫描、监控、停止、规则修改和错误拒绝通过 | 执行在电脑，不提供 Easy521 物理 I/O 或硬实时证明 |
| Easy521 的既有 ARM 对象 | 新增 Linux 变量绑定后处理，补齐两级链接后的 8 项符号差异 | 消费已有厂商编译对象，不是 Linux ST 前端或完整可下装应用 |
| 当前 WSL 原生 USB | 未见 USB sysfs 总线及 `/dev/bus/usb`，没有原生枚举该设备 | 不据此声称物理 USB 被拔掉；本轮不切换 USB 归属 |
| 清除设备程序 | 未执行 | 用户“删除代码”尚待澄清是烧录、主机清理还是清除设备程序 |

## 真实 Linux 编译、运行与监控

本机固定 MatIEC 的 commit 为 `3a41303bcb4e50403417bc39c63f88981d7d961e`，`iec2c` SHA-256 为 `4bd3cd311d03954a7cb6b834dc848829f6d9fcc79054f592525eeb8c42df3834`。本轮使用实际 `iec2c` 与 GCC，执行既有 CLI 验收脚本，而非由固定数据模拟 ST 执行结果。

- [基础闭环报告](../../../evidence/easy521-linux-wiring-20261019T045046Z/open-linux-validation.json)：坏 ST 由真实编译器拒绝；未部署构建不能运行；build→本地 flash→run→monitor→stop 成功；每扫描 +1 改为 +2 后，真实样本满足不同规则；部署后字节被改动时拒绝继续报告完整程序或重启。
- [AI 工作流报告](../../../evidence/easy521-linux-wiring-20261019T045046Z/ai-workflow-validation.json)：电机教学模型的启停/联锁/边沿/TON 场景、60→100 ms 规则修改先失败再按明确新需求更新场景通过；非法输入与语法错误拒绝；REAL 滞回和不同周期场景通过。场景数和断言是各次执行的子集，不能叠加成独立测试总数。
- 监控读取本地已校验运行实例的样本、源/构建/二进制身份、进程身份与新鲜度。当前 `monitor --target open-linux` 为单次快照，尚无专用持续 follow；它不代表 Easy521 机内监控。

可复跑的真实验收：

```bash
.venv/bin/python tools/check_open_linux.py \
  --toolchain-dir /home/zss/.cache/plcidf/matiec \
  --report /tmp/new-linux-validation.json

.venv/bin/python tools/check_ai_workflow.py \
  --toolchain-dir /home/zss/.cache/plcidf/matiec \
  --report /tmp/new-ai-workflow-validation.json
```

脚本只清理自己创建的临时工程；源项目、用户工程和设备程序不受其清理。当前没有 `plcidf clean` 命令，不能编造删除接口。日常 AI 工程与命令见 [AI 编程说明](../../AI_PROGRAMMING.md)。

## 新能力：从已有 GVT 分配补齐 8 项符号绑定

[研究 16](16-application-transaction-2026-10-19.md)已在 Linux 用 LLD 完成两级 ARM 可重定位链接，但与厂商产物规范化对照时还差 8 个变量的类型/地址。[研究 17](17-existing-variable-bindings-2026-10-19.md)独立解析既有 GVT 分配，并以 CSV 交叉核对；本轮将二者串成受限后处理工具 [bind_vendor_globals.py](../../../tools/bind_vendor_globals.py)。

普通全局的类型 8 和写入规则来自 `H530Converter.dll` 的 SInfo 构造与 `STRelocation.dll` 的静态证据。地址只来自 GVT，CSV 只核对，不读取参考 ELF 的地址，也不通过新变量分配算法产生地址。固定映射仅为既有八项 `g_iBESS_Mode/g_rBESS_*` 名称到已观察的 ASCII ELF 名称；没有把 `.upper()` 推广为通用名称规则。

### 输入合同

工具仅从源码仓库运行，使用相邻的 GVT/ELF 解析器；本轮没有把它注册成 `plcidf` 子命令或安装包资源。参数是本地文件：

```text
--source <声明关联的MAIN正文>
--gvt <既有合法变量表>
--address-csv <独立地址导出>
--link-report <本仓库成功的两级链接报告>
--association-manifest <显式哈希关联记录>
--output-dir <尚不存在的新私有目录>
```

关联 manifest 固定 profile，绑定源码、GVT、CSV、link report 及有序 POU/两库的 SHA-256，并带规范 JSON 自身哈希。工具重新读取 original/frozen 输入和两阶段产物，核对 ARM32 little-endian `ET_REL`、实际记录的有序命令/成功状态，以及原始文件没有变化。

**关联记录是调用者声明的批次哈希一致，不证明源码编译出了给定 POU。** 本次声明的 MAIN 正文为 `+2` 探针，SHA-256 `392ae08c3e1e2bbed430af326d287e042bf15d3dc8f674712a6ed748f40acc09`；消费的是研究 16 冻结对象。缺失的 source→POU 编译来源没有补造，结果恒 `source_compilation_verified=false`、`association_verified=hash-only-caller-declared-batch`。原始关联见 [binding-association.json](../../../evidence/easy521-linux-wiring-20261019T045046Z/binding-association.json)。

### 允许的变换

每个指定符号必须恰好一项，且原值严格为 `st_value=0、st_size=0、st_info=0x10、st_other=0、st_shndx=UND`。仅修改 `st_value` 四字节及 `st_info` 字节；类型低半字节写 8，保留 binding 和 UND，不修改代码、重定位、节索引或其他符号。已经绑定、重复、缺项、异常原值、未知 GVT profile、哈希漂移或路径冲突均拒绝。

产物只能写到新的私有目录，原文件不变；报告记录每项来源、修改偏移、前后值、哈希及尚未解析的运行时依赖。成功仅为 `existing-symbol-bindings-applied`，始终 `downloadable=false`、`runtime_abi_verified=false`。这些对象不能交给当前 flash 命令冒充完整应用。

## 本机冻结样本的实际验收

真实执行 [binding-invocation.json](../../../evidence/easy521-linux-wiring-20261019T045046Z/binding-invocation.json) exit 0，输出两对象及 [binding-report.json](../../../evidence/easy521-linux-wiring-20261019T045046Z/binding-report.json)。对象只保留于忽略的 `.plcidf/native-bind-20261019T045636Z/bound-objects/`，不提交厂商原文件或二进制副本。

随后由统筹独立解出符号和节，与另一份冻结厂商参考比较，见 [binding-reference-comparison.json](../../../evidence/easy521-linux-wiring-20261019T045046Z/binding-reference-comparison.json)：

| 对象 | 规范化符号项 | 与厂商参考的符号差异 | 本次修改字节 |
|---|---:|---:|---:|
| stuserprog.o | 137 | 0 | 39 |
| stprog.o | 1676 | 0 | 39 |

两项的代码、重定位及除字符串/符号表外各节与参考均吻合；BSS 只比较 NOBITS 类型和内存长度。对自身输入逐字节比较，变化严格位于选定 8 项的允许字段，输入哈希未变。最终文件仍不与厂商文件逐字节相同：字符串表/符号表布局等差异继续保留，没有通过复制参考文件填平差异。

## 剩余工作

Easy521 完整 Linux 路径仍需：ST 前端兼容与 source→POU 追溯、通用变量/保持区/任务/实例分配、名称与其他 SInfo 分支、完整配置/FOID 生成、下载会话/有序文件事务、设备字节校验、原生传输与实机执行。已有 ELF 后处理的成功不能替代这些验收。

[Linux 主机只读观察](../../../evidence/easy521-linux-wiring-20261019T045046Z/linux-host-observation.json)中，WSL 只有其虚拟网络接口，未见原生 USB 总线/设备节点；`lsusb` 也未安装。本轮没有为了枚举改内核、安装驱动、切换 USB 归属或扫描猜测 IP。没有已知 PLC 网络地址/服务时，不宣称已建立纯 Linux 真机监控。

负载接线另见 [A9/B9 与 TN/TP 说明](../../usage/EASY521_LOAD_WIRING.md)。当前负载变化与“删除代码”含义未明确，未清除 PLC 应用，也未追加下载、RUN/STOP 或输出操作。
