# Easy521 的 WSL CLI 实验入口

2026-10-19接入24 V后的实测：灯程序经现有`plcidf build`完整厂商编译零错误；统筹从WSL监督厂商桌面完成USB下装、独立程序比较和RUN。用户明确确认输出区Y0闪烁，STOP后熄灭。独立USB查询读到RUN时GCM=1、M8000=true及M8013变化；STOP时GCM=0、M8000=false、M8001=true。原始记录、人工反馈、构建失败和产物漂移见[20号报告](research/firmware/20-powered-led-bench-2026-10-19.md)。

这轮下载采用精确项目/对话框的统筹操作，仍调用Windows AutoShop与libusb0驱动。现有`AutoShopVendorAdapter`只接受其3840×2160校准布局，本次1920×1080布局和RUN中下载的双提示尚未接入该严格适配器；不能把原语成功当作`plcidf flash`已通过。下一批任务见[当前桌面适配计划](architecture/EASY521_CURRENT_DESKTOP_ADAPTER_PLAN_2026-10-19.md)。完整Linux原生编译、包装和无界面下载仍未完成。

## 固定状态与变量读取

以下命令在源码仓库根目录运行。`7-2`是本次台架定位，执行前应重新核对。

```bash
# 默认preview，不调用Windows、不获取设备租约、不访问USB。
python tools/read_easy521_usb_memory.py --profile revision
python tools/read_easy521_usb_memory.py --profile m8013

# AutoShop完整退出、fresh USB枚举唯一匹配后，显式做一次读取。
python tools/read_easy521_usb_memory.py --profile m8013 --execute --busid 7-2
```

新工具只提供10个固定项：`revision`、`step`、`scan-counter`、`confidence`、`published-scan`、`published-value`、`heartbeat`、`m8000`、`m8001`、`m8013`。地址/类型绑定现有工程分配快照；不接受任意地址，不写变量或控制CPU。Y0没有加入固定表，Y0闪烁来自用户目视确认。工具返回`candidate-observed`、保留原始帧及`hardware_verified=false`：调用成功不自动校准标量含义、程序身份、物理输出或精确扫描周期。不同变量逐项读取不是原子快照。协议来源见[19号报告](research/firmware/19-existing-variable-read-2026-10-19.md)，实际观测另见20号。

下面保留状态查询入口。此前ff010000/4B的u32=0属于另一条EtherCAT相关索引，不能解释为CPU状态；静态调用链已由[18号报告](research/firmware/18-runtime-mode-correction-2026-10-19.md)更正。

```bash
# 默认纯预览。实际读取需确认当次BUSID、关闭AutoShop，再显式执行。
.venv/bin/python tools/read_easy521_usb_runtime.py --profile gcm-state
.venv/bin/python tools/read_easy521_usb_runtime.py --profile gcm-state --execute --busid 7-2
```

worker会在设备锁内重新枚举核对；不自动关闭AutoShop、不切换USB归属、不重试。成功为`observed`，提供raw_value及单独的厂商静态解释；通用工具不会因本轮台架证据而自动提升其他设备的身份或校准等级。默认`literal`保持历史ff010000/4B请求。固定内存读取的reader、codec和helper已随wheel作为资源分发；状态查询脚本当前仍从源码仓库运行。这些工具未添加为新的`plcidf`子命令。

2026-10-17 已真实完成两个内存探针的应用下载、厂商程序一致性正反校验。当时 CPU RUN 被拒绝，未完成运行验收；用户报告额定 24V 主供电未连接。完整记录和证据见 [该轮设备报告](management/EASY521_APPLICATION_2026-10-17.md)，不代表今天重新观察了供电或 CPU 状态。

2026-10-18 更新：普通受限正文和显式基础类型全局表已可不经过 AutoShop 界面生成中间输入并真实编译 ARM 对象；本地 intent/release 追溯与诊断已补齐。完整链接/设备字节绑定仍开放，当前供电未重新观察。见 [新交付报告](management/HOST_TOOLCHAIN_DELIVERY_2026-10-18.md) 和 [离线诊断](usage/easy521-doctor.md)。

直接数据控制入口另见 [Easy521 直接控制研究与用法](management/EASY521_DIRECT_CONTROL_2026-10-18.md)：仓库内新增 Linux D 区单次读写/读回工具；它要求真实 IP 和明确地址用途，写默认预览。本轮网口无链路，尚未实测 Easy521 Modbus；数据读写不替代 CPU RUN 或新 ST 应用下载。

当前路线保留已安装 Windows AutoShop 4.12.0.2 和交互桌面，由 WSL 发起命令。下载适配器只接受本次标定的 3840×2160、应用 DPI 96/系统 DPI 192、最大化 MAIN 窗口；其他布局拒绝。它还不能支持任意 AutoShop 版本或其他 PLC 型号。开放 Linux 的 [AI 场景验收](AI_PROGRAMMING.md) 可独立使用。

## 构建记录

在配置中指定独立工作副本，AutoShop 打开该工程的已保存 MAIN，并退出在线监控。`--source` 是 MAIN 正文，不含 PROGRAM 包装。现阶段变量、库和任务需在现有厂商工程中已正确配置。

```bash
plcidf build --source examples/easy521_native_probe/MAIN_plus2_body.st \
  --working-project 'E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV' \
  --build-record /tmp/easy521-build.json \
  --execute --i-have-authorized --json
```

`--build-record` 保存完整编译结果，包括真实零错误诊断、同步记录和产物清单。旧 `--manifest` 只保存原始产物清单，不能代替完整记录。上述路径对应本机工作副本，使用其他工程时须同时修改配置及命令。

## 下载与校验

`backup.json` 必须记录已从目标上载的 ZIP、SHA-256、USB 身份、文件数；`field-facts.json` 记录观察来源、带时区的 `observed_at`、供电、输出隔离、物理开关和应用范围。格式参考 [本次备份记录](../evidence/easy521-application-20261017/backup-record.json) 和 [现场事实](../evidence/easy521-application-20261017/field-facts.json)，历史事实不能冒充下一次现场观察。

```bash
plcidf flash --build-record /tmp/easy521-build.json \
  --backup-record /tmp/backup.json --field-facts /tmp/field-facts.json \
  --working-project 'E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV' --json
```

上面默认预览。用户已授权的实际操作加 `--execute --i-have-authorized`。下载不自动 RUN，也不自动重试。`unknown` 或非零退出码不能推出设备未改变，必须先读回日志/设备状态。

**2026-10-17的严格CLI真机流程返回 unknown，该边界仍保留。** 厂商下载及程序比较会重新生成本地文件，旧清单因此失效。独立厂商比较通过，不能将原先选择的精确二进制追溯标成通过。2026-10-19灯程序再次留下两项对象漂移证据；解决实际下载对象的字节绑定前，此严格入口仍为实验性。

## 只读变量快照

先在 AutoShop 中打开实际在线变量表，再调用：

```bash
plcidf monitor --transport vendor-usb --timeout 20 --json
```

可用 `--process-id`/`--grid-handle` 对歧义表格作精确选择。命令只读取 USB 枚举和现有窗口属性，不发 RUN/STOP、PLC 写命令或导航登录流程。当前 `observe_connection` 没有独立在线身份读回，因此即使 `values_read=true`，仍返回 `online=false`、`connection_verified=false`、`plc_state=unverified` 和退出 1；不可将可读 UI 缓存等同于新鲜设备数据。运行状态还需要有效的 M8000/M8001 当前值和独立连接证据。

## 脱离 GUI 的编译研究

独立 STLang worker 已将冻结的厂商 MAIN.st/MAIN.cl 转为新的 ARM `.o`，不访问设备。它仍依赖 Windows 厂商编译器，尚不接受普通 IEC 文本生成完整设备应用，见 [真实正负编译实验](research/firmware/10-stlang-headless-compiler-2026-10-17.md)。

新增 `prepare_vendor_st.py` 可将普通 MAIN 正文及显式 BOOL/INT/DINT/REAL 全局表生成合法 MAIN.st/cl；已真实验证多语句、IF、规则变化及错误语法。仍只生成 ARM 可重定位对象，完整工程链接和下装不是该工具的交付范围。使用方法及隐式运行时声明见 [研究报告 11](research/firmware/11-plain-main-object-2026-10-18.md)。两个 worker 均随 wheel 分发，`doctor --json` 给出它们的实际路径和 argv。
