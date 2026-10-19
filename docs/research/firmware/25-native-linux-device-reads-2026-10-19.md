# Easy521 原生 Linux 真机读取、CDC 接口更正与主机恢复

2026-10-19，实际读取窗口为 UTC 06:54:20～06:54:21（上海 14:54:20～14:54:21）。用户明确回复“已共享”后，统筹完成 WSL 附加和 **8 次原生 libusb 固定读取**。状态首尾 GCM 都为 1，M8000=true、M8001=false，符合 RUN 的既有解释；M8013 四次为 true→false→true→false。它证明本轮原生读取与时钟变化，不是完整原生应用构建、下装或物理输出验收。

本报告更正[24号](24-linux-native-usb-2026-10-19.md)中把 Windows helper 的 claim0 外推为 Linux bulk 接口0的假设，旧报告和旧测试证据原样保留。新的[原始证据目录](../../../evidence/easy521-native-device-20261019T064323Z/)及[会话摘要](../../../evidence/easy521-native-device-20261019T064323Z/session-summary.json)记录本轮新事实。

## 设备归属与真实描述符

1. 新鲜 Windows 枚举确认唯一 `0471:a531` 在 `7-2 / Shared`，AutoShop 未运行。设备租约内只执行一次 `usbipd attach --wsl Ubuntu --busid 7-2`，无 auto-attach。命令返回0时瞬时 Linux 目标未出现，原记录保留 unknown；随后只读观察确认 Windows Attached 与 Linux `001:002`，没有重复附加。
2. USB 自述为 `Linux 3.10.65 with sunxi_usb_udc` / `Easy52x USB Device v2.4`、bcdDevice `0310`，无 USB 序列号。它们不能确定准确 SoC、永久 CPU 身份或当前应用字节。Easy521 TN 来自用户确认，不是 USB 产品字符串解出的精确型号。
3. [真实描述符](../../../evidence/easy521-native-device-20261019T064323Z/native-descriptors.json)共93字节，SHA-256 `be0da4a2a37147d2e0068bb492bf0eb89ea067e2808ecdcf6a756b0ddbd51351`。其中配置长度75字节、配置值2、两个接口：

| 归属 | class / subclass / protocol | 端点 |
|---|---|---|
| CDC控制接口0，alt0 | `02 / 02 / 01` | interrupt IN `84`，10字节 |
| CDC数据接口1，alt0 | `0a / 00 / 00` | bulk IN `81`、OUT `02`，各512字节 |

IAD `080b000202020107` 关联接口0、1；CDC union `0524060001` 明确 master0/slave1。Windows libusb0 的 claim0 成功不能证明 bulk 端点属于 Linux 接口0。修正后的[原生后端](../../../tools/easy521_linux_usb.py)严格验证该实际关联，再检查、claim、release **数据接口1**；不切配置或自动解绑内核驱动。

## 发送前检查、显式主机准备与恢复

Linux 自动把两个接口交给 `cdc_acm`，USB 节点初始为 root:root、0664。先用普通用户执行修正读取器：真实 libusb 描述符检查通过，在 open 阶段返回权限错误 -3，`write_calls=0/request_outcome=not_sent`，见[原始失败](../../../evidence/easy521-native-device-20261019T064323Z/native-before-host-prepare.json)。这是原权限下的新检查，不覆盖此前无目标实验。

统筹随后执行本轮固定设备的[显式主机实验助手](../../../evidence/easy521-native-device-20261019T064323Z/run_native_read_session.py)：

- 核对当前位置、VID/PID、配置和完整描述符哈希，记录节点原 owner/group/mode/rdev 与两个 driver 链接。
- 从控制接口的真实 sysfs 归属找到唯一 TTY，并以 root fuser 确认无人使用；未卸载全局驱动。
- 临时将这一个 USB 节点 owner 改为普通用户1000；先实证读取器在 driver occupied 时拒绝，仍未发 PLC 帧，见[占用拒绝](../../../evidence/easy521-native-device-20261019T064323Z/00-runtime-gcm-state.json)。
- 显式 unbind 本设备控制接口 `1-1:2.0`，确认其控制/数据接口都已释放，再以普通用户运行原生读取器。
- finally 重绑原 CDC 驱动，并恢复节点原 owner/group/mode；本轮两项均实测恢复，且无清理错误。设备保持 WSL Attached，没有自动转回 Windows。

助手设120秒会话截止、子进程各12秒及外层150秒；SIGTERM/SIGHUP/SIGINT/SIGALRM进入清理。不可捕获中断或失电不能保证 finally 执行，另有 `--restore-only` 固定身份恢复入口；本轮未需要该恢复路径。独立审阅发现并修复了初稿超时预算和固定TTY检查问题，然后才执行实验。

主机准备本身不能说成“零USB通信”：Linux CDC 驱动 probe/rebind 会进行类控制请求。读取器不请求PLC变量写入、CPU模式改变、应用下装或固件操作。驱动机制依据[Linux官方CDC源码](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/refs/tags/v6.6/drivers/usb/class/cdc-acm.c)，claim所属接口依据[libusb官方接口](https://libusb.sourceforge.io/api-1.0/group__libusb__dev.html)。

## 原生读取的实际结果

[guard_native_read.py](../../../evidence/easy521-native-device-20261019T064323Z/guard_native_read.py)在普通用户读取进程中拒绝 Python subprocess、exec/system 和 socket audit 事件，防止调用 Windows 或网络回退。host侧的 usbipd/WSL launcher 仅用于归属与准备；WSL底层USB/IP仍依赖Windows主机服务，不能据此声称整台WSL没有Windows依赖。

| 顺序 | 固定项 | 实际结果 |
|---:|---|---|
| 1 | GCM状态 | raw 1 |
| 2 | M8000 | raw `01`，BOOL候选 true |
| 3 | M8001 | raw `00`，BOOL候选 false |
| 4～7 | M8013 | `01 → 00 → 01 → 00` |
| 8 | GCM状态 | raw 1 |

每次请求均只有一次OUT，均在接口1完成并收到codec关联的有效回复；8个序列值互不相同，实际运行源码哈希复核未变。M8013中间观察按350ms间隔计划，另含进程/USB耗时；这些不是失败重试，遇任一失败会停止后续请求。本轮全部成功且release/close完成，详见[会话原始记录](../../../evidence/easy521-native-device-20261019T064323Z/native-read-session.json)及[调用/超时/源码身份](../../../evidence/easy521-native-device-20261019T064323Z/session-invocation.json)。

读取非原子，不能由这几次样本确定精确扫描周期、完整时钟频率或物理Y0端子状态。当前应用字节和CPU永久身份为unknown；没有把[此前灯程序部署](20-powered-led-bench-2026-10-19.md)当作本轮重新核验。通用reader仍保留candidate和未校准标记，本轮摘要只提升“本设备、此会话的固定读传输”这一层。

## 软件验证与使用边界

修正测试用真实配置描述符构造libusb C树，覆盖缺失/矛盾/重复IAD或union、bulk错接口、类/端点/packet错误以及extras位置差异。统筹完整工具回归启用真实LLD/LLVM，630个顶层测试通过；分发配置守卫13项通过；sdist→wheel→仓库外guarded preview及真实motor 6场景/56断言通过。代理分项389项/native81项均是整套子集，不重复相加。独立审阅未见剩余P0/P1。

普通读取命令仍要求设备节点可访问且接口空闲；默认preview及实际执行都不会自动更改权限或解绑。这里的实验助手固定于本次位置和指纹，**不是通用安装/自动监控入口**，不要照搬旧bus/address。持续监控、受控host onboarding、完整应用生成和原生下载事务继续开放；本轮没有新增程序下装、变量写入、CPU RUN/STOP或输出动作。
