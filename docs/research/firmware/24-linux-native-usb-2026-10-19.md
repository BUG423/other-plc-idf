# Easy521 原生 Linux USB 只读后端与本机前置验证

2026-10-19。本批由三条 AI 子代理工作线并行实施：Linux USB、配置 FOID、受限 ST/ARM ABI；统筹负责集成、独立验证和 GitHub 同步。直接在唯一 `main` 分支协作，没有建立其他分支或 worktree。

## 已新增的行为

[easy521_linux_usb.py](../../../tools/easy521_linux_usb.py) 使用 Python 标准库 ctypes 调用系统 `libusb-1.0.so.0`。现有[状态读取器](../../../tools/read_easy521_usb_runtime.py)和[固定内存读取器](../../../tools/read_easy521_usb_memory.py)增加显式 `--transport linux-native`；不指定时继续使用原 Windows 后端。

原生后端复用已有严格协议 codec，仅允许固定读取 profile。状态路径为 `literal` / `gcm-state`；内存路径保留既有十项。它不提供任意帧、变量写入、CPU 模式改变、应用下载或固件操作。发送到 OUT 端点的是读取请求，不是 PLC 业务数据写入。

- 默认 preview 不加载 libusb、不枚举、不获取设备租约、不启动 Windows 或其他外部进程。
- 执行必须显式指定 Linux `BUS:ADDRESS`；它与 Windows 的 `7-2` 这类 BUSID 不是同一个命名空间。
- 新鲜枚举要求唯一 `0471:a531`，位置同时匹配；只接受已处于配置 2、接口 0、alternate setting 0、bulk OUT `0x02` / IN `0x81` 的设备。来源为研究 15、18、19 与已有 Windows helper；执行时核对描述符和现有配置。
- 不自动设置配置、切换 alternate setting、解绑内核驱动、reset、clear-halt、重连或重放。接口占用或合同不符时拒绝。
- 每个请求仅一次 bulk OUT；分块接收最多四次，共用接收截止时间。`--timeout-ms` 为 1～10000，默认 2500；发送及接收各自有这一预算。标准 GET_CONFIGURATION 上限为 1000 ms。libusb 的枚举/open/claim 等主机资源 API 不提供超时参数，调用者需要更强的整进程上限时应外加 `timeout`。
- 发送发起后先记 `unknown`，超时保留实际传输长度、部分回复及错误；收到不完整、错误或无法关联的帧不报成功。清理错误也不能提升为有效观察。

内存结果继续为 `candidate-observed`；GCM 的 RUN/STOP 映射单列为静态解释。通用工具不因历史台架读到过同一数值就提升本次目标的身份、程序、运行或标量校准等级。

## 使用与设备归属

只预览，不需要设备或 libusb 安装：

```bash
.venv/bin/python tools/read_easy521_usb_runtime.py --transport linux-native --profile gcm-state
.venv/bin/python tools/read_easy521_usb_memory.py --transport linux-native --profile m8013
```

原生 Linux 需要系统 libusb 与相应 USB 设备权限。WSL 还需将设备共享并附加到 WSL。微软说明 `usbipd bind` 需要 Windows 管理员，而后续 `attach --wsl` 不需要管理员；附加期间 Windows 无法同时使用该 USB 设备。必须先用新鲜 `usbipd list` 核对位置和产品，再按[微软 WSL USB 文档](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)操作；读取器不会替用户切换归属。

附加后从 Linux USB 描述符核对 VID/PID、busnum/devnum，或用 `lsusb` 定位。将真实位置赋给 `PLC_USB` 后，才执行：

```bash
timeout 20 .venv/bin/python tools/read_easy521_usb_runtime.py \
  --transport linux-native --profile gcm-state \
  --linux-device "$PLC_USB" --timeout-ms 2500 --execute
```

上述 `timeout` 为外层进程上限；如果外层杀死进程，不能凭没有 JSON 推断请求没有发出。应保留该调用为未知，不自动重放。工具保持单次读取，不会因缺失原生设备而回退到 Windows。

## 本机新检查与验证范围

证据目录为 [easy521-native-parallel-20261019T062312Z](../../../evidence/easy521-native-parallel-20261019T062312Z/)。

1. 本次 Windows 枚举唯一 `0471:a531` 在 `7-2 / Not shared`；AutoShop 未运行，当前 Windows 会话不是管理员。这些是 USB 定位与主机事实，不是 CPU 永久身份或供电状态证明。
2. WSL 内核已有 USB/IP 模块和 libusb，起初模块未加载，USB sysfs/devfs 不存在。统筹通过发行版 root 正常加载 `vhci-hcd`，成功出现 USB 总线/节点；只见两个 Linux root hubs。该动作不发送 PLC 请求，见 [host-preflight.json](../../../evidence/easy521-native-parallel-20261019T062312Z/host-preflight.json)。
3. 统筹用真实 libusb 执行无目标负例：指定不存在的位置，枚举后拒绝，`write_calls=0`、`request_outcome=not_sent`，列表和 context 均释放。见 [原始结果](../../../evidence/easy521-native-parallel-20261019T062312Z/native-usb-no-target.json)与[调用/源码哈希](../../../evidence/easy521-native-parallel-20261019T062312Z/native-usb-no-target-validation.json)。该证据绑定当时源码；后续仅修正失败顶层 stage 的提交不能倒填到旧哈希。

修正失败顶层 stage 后，统筹在新文件中重验当前版本，仍在枚举阶段拒绝、未发送请求；见[最终原始结果](../../../evidence/easy521-native-parallel-20261019T062312Z/native-usb-no-target-final.json)与[最终哈希/调用验证](../../../evidence/easy521-native-parallel-20261019T062312Z/native-usb-no-target-final-validation.json)。旧记录没有覆盖。

统筹另将两份新ST源码实际编译，再消费其新ARM对象完成两级LLD链接。初始摘要中的链接输入哈希字段为null；随后按真实的`inputs[0].inspection.sha256`独立核验并写[补充关联记录](../../../evidence/easy521-native-parallel-20261019T062312Z/native-st-source-link-association.json)。两份源码、编译对象和链接冻结POU哈希均对应，完整应用和runtime ABI继续未验证；不是给旧厂商对象追加一个源码哈希就算编译来源成立。

离线 fake libusb 覆盖 ctypes 指针输出、描述符树、唯一匹配、配置/端点/内核驱动拒绝、partial OUT、partial timeout IN、分块预算、错误/不关联帧、资源清理、租约与中断。它与真实主机枚举、未来真机读取分别验收。两个 reader、codec、原生 module 和既有 Windows helper 均进入 sdist/wheel；独立安装验证会在禁止库加载、进程、网络和设备租约的守卫下运行预览。

**本批尚未完成 Easy521 原生 Linux 真机读取或应用下装。**设备目前仍由 Windows 持有，已向用户请求完成管理员共享步骤；收到反馈后需重新枚举核对，不能照搬历史定位。当前没有新增 CPU RUN/STOP、PLC 变量写入、应用删除或下载。完整原生应用构建仍需变量/任务分配、配置 payload 生成和下载事务；另见[FOID 研究 22](22-linux-foid-packaging-2026-10-19.md)和[受限 ST/ARM 研究 23](23-linux-st-abi-2026-10-19.md)。

## 依据

- [libusb 同步 I/O 官方文档](https://libusb.sourceforge.io/api-1.0/group__libusb__syncio.html)：毫秒超时、零值表示无限、超时也可能已传输部分数据。
- [libusb 描述符和设备操作官方文档](https://libusb.sourceforge.io/api-1.0/group__libusb__dev.html)：ctypes 结构按宿主 C ABI 对齐，不作为 packed wire frame。
- [微软 WSL USB 文档](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)：共享、附加和 Windows/WSL 独占条件。
- [此前真机报告 20](20-powered-led-bench-2026-10-19.md)：Windows 后端的 Y0/CPU/时钟观测，不能代替本原生后端验收。
