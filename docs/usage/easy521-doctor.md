# Easy521 离线诊断与下一步

```bash
plcidf doctor --target easy521 --json > doctor.json
```

`doctor` 只读取本地文件、PE 版本资源、Python 包版本及适配器声明。它不启动 Windows 程序，不加载厂商 DLL，不枚举 USB，不读取 GUI，不访问 PLC，也不发起编译、下载或 RUN。该命令没有 `--live` 参数。当前连接和运行状态需要独立观察。

stdout 继续使用 `schema_version: 1`，增加 `easy521` 节，其 schema 为 `plcidf.easy521-capability-diagnostics.v1`。退出 0 仍只表示本地 Modbus 监测及软件模拟所需条件齐全；请继续检查 `easy521.capabilities`，不能把退出 0 理解为全部厂商能力已可用。

## 看哪些字段

| 字段 | 含义 |
| --- | --- |
| `resources` | 配置/包内脚本位置、是否存在及所读脚本哈希；同时检查 native reader、对象编译 helper、两个 Python worker |
| `easy521.vendor.on_disk_autoshop` | 从磁盘 PE 的 RT_VERSION/VS_FIXEDFILEINFO 读取的 FileVersion；解析不确定时保留 unknown |
| `easy521.vendor.adapter_metadata` | 代码支持的厂商窗口版本与下载/比较能力声明；不是当前设备验收记录 |
| `easy521.vendor.required_layout` | 下载/比较适配器要求的布局、屏幕及 DPI；`current_layout_verified` 默认 false |
| `easy521.vendor.object_compilers` | 从实际 helper 提取的受支持路径和 SHA256，当前文件哈希是否匹配、相邻 Kernel.dll 是否存在 |
| `easy521.capabilities.*.local_prerequisites` | 本地文件条件；不会证明 Windows 的动态依赖已经成功加载 |
| `easy521.capabilities.*.missing_conditions` | 本地缺失项，以及尚未观察或尚未实现的证据条件 |
| `easy521.capabilities.download.prepared_release` | 本地 intent/candidate 冻结与哈希跟踪能力；当前 `release_hash` 不预填，不证明设备二进制绑定 |
| `easy521.next_steps` | 有边界说明的建议与 argv；doctor 只展示，不执行 |

磁盘 FileVersion 与 AutoShop 窗口标题版本可能不同。例如本机检查发现 FileVersion 为 `4.12.0.0`，适配器校准的窗口版本为 `4.12.0.2`。这是不同来源的版本字符串，不能仅据它们不同断定不兼容。`running_autoshop_version` 与 `plc_firmware_version` 不会从历史资料预填。

## 分开判断七种能力

| 能力 | 已实现范围 | 仍须单独确认 |
| --- | --- | --- |
| `source_build` | 已有原生工程的 MAIN 正文同步、厂商编译及产物核验 | 当前精确工程、可写 MAIN、新产物和厂商零错误诊断；不创建完整厂商工程或导入变量 |
| `vendor_object_only` | 受限正文和显式全局声明准备中间输入，再通过 StLang 编译一个 ARM `.o` | 输入子集、原厂编译诊断及新对象；没有完整应用链接、封装或设备部署 |
| `download` | NativeDeploy 单次下载事务及厂商结果校验 | 本次 build record、上传备份、现场事实、目标、布局、日志新鲜度及产物完整性 |
| `source_consistency` | 厂商工程/源码一致性结果 | 必须是本次新结果；自动重编译不能被当成原构建字节仍相同 |
| `device_binary_identity` | 尚无已标定的设备可执行字节/应用哈希读回接口 | 源码一致、下载成功及运行标志均不能代替设备二进制身份 |
| `runtime` | 运行位解析及内存探针扫描/心跳样本检查 | 当前连接身份、实际运行位、扫描进展及程序绑定；没有 Easy521 RUN/STOP CLI 控制能力 |
| `monitor_vendor_usb` | 有限时长、只读 MSAA 在线表取值 | 设备在线身份与状态来源仍须独立核验；缺运行位时保持未核验 |

`implemented: true` 表示存在软件路径；`current_verification: not-observed` 表示 doctor 没有执行该路径。查看具体 `missing_conditions` 后再安排后续操作，不能用历史正例抹掉当前缺口。

`prepared_release` 能跟踪本地冻结的工程意图及候选产物。厂商后续比较可能重新编译，比较通过至多支持工程/源码一致性；保留的候选快照与设备实际可执行字节是否相同仍为 `unverified`。doctor 的 `verification_levels` 分列 local_candidate、project、binary、runtime，均不会借用先前试验填成已验证。

旧的 `capabilities.build/flash` 现在指向原生构建/部署路径。旧桥接标志保留在它们的 `legacy_bridge` 子项，避免旧桥接尚未实现结果校验造成“整个原生后端也没实现”的误报。

## 不占用 GUI 的对象编译准备

`vendor_object_only.prepare_argv` 和 `compile_argv` 给出当前环境中真实的 worker 路径及参数。wheel 同时带有两个 Python worker 和 PowerShell helper，源码工作区和独立安装使用同一份文件。

输入是无 PROGRAM/VAR 包装的 UTF-8 正文，例如：

```st
COUNTER := COUNTER + 1;
```

显式声明 JSON：

```json
{"schema":"plcidf.vendor-main-source.v1","globals":{"COUNTER":"DINT"}}
```

准备工具支持明确列出的 BOOL/INT/DINT/REAL 全局变量和基础控制/运算子集，拒绝直接 I/O、软元件、库调用和完整工程包装。准备成功仅表示中间输入已经生成，语法/类型的最终裁决仍由厂商编译器完成。变量最终存储、完整链接和运行绑定不由该对象实验解决。

准备目录和对象输出目录都必须使用尚不存在的新目录，父目录须存在。两个 argv 中的 `NEW` 是待替换名称。准备阶段只运行 Python；编译阶段调用 Windows StLang 与 helper，不访问 GUI/PLC。`compile_argv` 显式包含 `--powershell-exe`，WSL 无需把 powershell.exe 放进 PATH。

## 只读观察与当前状态

已有的显式只读入口：

```bash
plcidf monitor --target easy521 --transport vendor-usb --once --timeout 10 --json
```

它有统一的总时间预算。请分别看 `values_read`、`connection_verified`、`online`、`plc_state`：可读数值可能是缓存，USB 枚举存在也不证明厂商会话连接到正确 PLC。当前缺少独立设备在线身份读回时，读到了值仍可返回未核验和非零退出码。

doctor 不采集当前供电、模式开关或输出隔离事实，不判定 USB 供电导致运行失败。本轮诊断与对象准备不要求或触发下载、RUN。真机二进制、运行及 I/O 验收由独立步骤取得证据。
