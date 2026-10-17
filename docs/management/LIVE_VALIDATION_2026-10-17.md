# Easy521 现场验证与软件交付记录

日期：2026-10-17（Asia/Shanghai）。用户本轮已授权推进真机实测；本次实际设备访问包括 USB 枚举、厂商设备信息读取及程序上载备份，未下载程序、未发送 RUN/STOP、未改 IP、未刷固件。

## 1. 实际结果

| 项目 | 结果 | 直接证据 |
|---|---|---|
| Windows USB 枚举 | BUSID `6-3`、`0471:a531`、H5U USB Device；设备在 Windows 侧 | [设备 JSON](../../evidence/hardware-20261017/cli-devices.json) |
| 厂商 USB 设备查询 | 厂商汇川技术，PLC，Easy521，固件 `6.11.0.1` | [现场查询](../../evidence/hardware-20261017/2026-10-17T102619.030172_0000-plc-info-query.json) |
| USB 程序上载备份 | 新建 Easy521/LiteST 工程后，厂商日志明确“上载工程成功”；62 文件及本地 ZIP 已记录 SHA-256，读回 MAIN 与仓库正文一致 | [上载日志](../../evidence/hardware-20261017/2026-10-17T104527.384689_0000-backup-transfer-log.json)、[备份清单](../../evidence/hardware-20261017/usb-upload-backup-manifest.json) |
| Windows 工具 | AutoShop `4.12.0.2`，当前存在交互桌面 | [初始状态](../../evidence/hardware-20261017/host-interfaces.json) |
| 以太网物理链路 | **未接通**：Realtek USB GbE，接口 8，`Disconnected`、`0 bps` | [提交前再次复核](../../evidence/hardware-20261017/network-pre-submit.json) |
| 网口 IPv4 | 初始为 `169.254.25.85/16`，没有据此识别出 PLC IP；未作 LAN 扫描或假连接测试 | 初始状态 JSON |
| MAIN 文本同步 | 精确工程/文档匹配；源码独立读回、保存、再次核对；diff 可读 | [只读 sync 预览](../../evidence/hardware-20261017/cli-sync-preview.json)、正负构建记录 |
| 故意错误源码 | `PLCIDF_NEGATIVE_TEST := ;` 被定位到第 26 行，厂商日志 1 错误；CLI 返回 `failed`、退出 1 | [负向 CLI 记录](../../evidence/hardware-20261017/cli-build-negative-final-guard.json) |
| 正确源码 | 新产物齐全、`Success!`、ARM ELF 和明确 0 错误日志共同通过；CLI 返回 `verified`、退出 0 | [正向 CLI 记录](../../evidence/hardware-20261017/cli-build-positive-final-guard.json)、[产物清单](../../evidence/hardware-20261017/build-manifest-final-guard.json) |
| PLC 程序下载、运行、真实网口监测与周期 | **未验收**；未以模拟场景或编译通过代替运行证据 | 本报告与开放任务 T08/T09 |

`usbipd list` 另提示服务未运行；本次 USB 使用 Windows 厂商驱动，无需将设备 attach 到 WSL。没有为解决这条警告而重启、改服务或抢占设备。

## 2. 工程保护与实测方法

原工程：`E:\PLCProjects\BESS_E521_DEV`。先复制文件，再从副本 `.hcpp` 让 AutoShop 自行解包为可打开的原生工程，最终活动工作副本为：

```text
E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV
```

本地 `.plcidf.toml` 已指向该副本；原配置另存 `/tmp/plcidf-local-config-before-live.toml`。本地监测配置仍为回环演示地址，不冒充 PLC 网口地址。

原工程 120 文件的快照见 [original-project-snapshot.json](../../evidence/hardware-20261017/original-project-snapshot.json)，[提交前逐文件复核](../../evidence/hardware-20261017/original-project-final-verification.json) 未发生变化。原生 `.hcp/.ST` 是厂商容器，不用纯文本覆盖；同步通过目标编辑器完成。打开的 `.hcp` 被 AutoShop 独占锁定，构建清单记录实际可读的原生源码/配置，排除该锁定归档及生成目录。

设备上载另存为 `E:\PLCProjects\PLC_USB_BACKUP_20261017_1845`，归档为同目录名的 `.zip`。选择“不覆盖当前工程”，明确选 Easy500/Easy521 与 LiteST 后由上载流程生成。完整备份留在本机，GitHub 保存文件哈希、厂商成功日志和 MAIN 读回证据；未进行恢复演练，不能承诺还原已经验证。上载后已重新打开原编译工作副本 MAIN，并读回确认。此流程目前通过 WSL 驱动厂商 GUI 完成，尚未提供通用 `plcidf backup` 命令。

现场适配中出现过未保存编辑器内容被清空的问题：PowerShell 将传给 C# 的 null 字符串转为空字符串，旧读取分支误触替换。已改为独立布尔 `replace`，恢复窗口内容后以“不保存”关闭原工程，再从副本重新打开；原磁盘源码和 PLC 程序均未改变。另修复实际标题 `[MAIN *]` 的空格适配并补回归。后续只读获取与保存读回均通过实测。

AutoShop 4.12.0.2 的日志为 owner-drawn ListBox。已验证其 item data 指向 UTF-16 日志行，通过该控件的有界只读缓冲区读取可取得完整日志，不依赖 OCR 判 0 错误。此适配只在已校准版本启用；其他版本或读取失败时保留截图/OCR，无法确认则 `unverified`。OCR 不是单独的成功依据。

构建前将工作副本中的旧 `MAIN.rst/MAIN.o`、`Temp/stprog.o`、`Output.prg/Output.sdt` 移入 `.plcidf/quarantine/<id>`；F7 只发送一次。新产物稳定出现后同时校验 compiler marker、ELF 架构、日志错误数与结束标记、编辑器文本和工程身份，才生成包含 SHA-256 的构建清单。构建验证不代表设备接受了这些产物。

## 3. 本轮可复现命令

已配置的本机环境，先确保 AutoShop 打开的正是上述工作副本的 MAIN，且无未保存编辑：

```bash
plcidf doctor --json
plcidf devices --json
plcidf sync --source plc/easy521/MAIN_5ms_body.st --json
plcidf build --source plc/easy521/MAIN_5ms_body.st \
  --working-project 'E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV' \
  --execute --i-have-authorized --manifest build-output.json --json
```

`sync` 默认只读 diff；实际同步需要 `--execute --i-have-authorized` 以及配置布尔 `bridge.enabled=true`。当前仅同步已有工程的 MAIN 正文，不自动导入新增变量、配置任务周期或新建原生工程；变量 CSV、工程配置与源码必须相容。

无硬件的文本与软件链路：

```bash
plcidf new /tmp/my_plc_project --json
plcidf check /tmp/my_plc_project --json
plcidf sim --with-monitor --once --calibrate
```

独立 wheel 已补资源与运行依赖，通过 sdist→wheel→全新 venv→仓库外运行验收。安装包位于本地 `dist/`，具体哈希与实际测试范围见 [分发证据](evidence/distribution-20261017.json)。CI 只做软件、mock 与回环网络测试，不访问 PLC。

本地全套软件回归：**346 passed、1 deselected**；另有 **5 项** GitHub 汇报脚本测试通过。命令为 `.venv/bin/python -m pytest src/plc_idf/tests wsl-tools/tests -k 'not test_devices_real_readonly' -o addopts= -q`。排除的是可能实际执行 USB 枚举的用例，硬件访问另记于上表。独立复核另发现同工程切换 POU 的写入窗口，已在 Windows 写入前重新核对精确工程和无未保存改动的 MAIN；日志及激活后的按键也重新核对工程。[10 项生产标题校验函数测试](evidence/autoshop-title-guard-20261017.json) 全部通过，且未执行 GUI/设备访问。

## 4. 尚需补齐的现场条件和功能

1. **网口链路**：核对 PLC 的独立 24V 供电、网线两端及 EtherNET1/2 通信口的灯。手册明确以太网口不支持 PoE；Type-C 可枚举不足以确认供电、端口和运行条件。依据本机 `Easy系列可编程逻辑控制器用户手册.PDF` 第 138–139 页；本次未测量供电，不将其推断为已确证根因。
2. **实际运行条件**：用户对当前输出隔离、在设备旁观察和 RUN/STOP 开关状态的核对尚未返回。本轮未因此开始下载或运行控制。较早历史隔离记录不当作本次现场状态证明。
3. **真实网络验收**：链路恢复后确定两个端口实际 IP、电脑网卡地址与路由，绕开 Meta TUN 假连接影响，再验证 Modbus 响应、已知值字序和工程映射。当前无可承诺的 PLC 网口 IP，不猜测扫描其他网络。
4. **部署事务**：已补严格 BUSID+VID/PID、跨进程租约、严格证据及产物/计划哈希模块；完整 flash 入口仍未贯穿新部署清单和厂商设备端一致性复核。旧 GUI 调用链仍只能报告未核验，不能作为完成下载验收的证据。
5. **备份与验收**：第一次查看上载入口后取消；随后在新建且明确型号的目录中完成 USB 上载备份。还需在核对隔离和开关状态后执行应用下载、一致性和运行/周期测试；不使用仅限模拟器的 TEST 工程。
6. **范围**：目前没有实现任意工程的自动变量/任务配置同步、用户 ST 的 Linux 仿真、通用断点调试或完全去 Windows。现有 `new/check/sync/build` 的能力边界均需保留。

本轮工作分工仍为 AI 技术执行代理。硬件材料与软件代码均入库供复核；T08/T09 的现场验收保持开放，不能因为软件 CI 通过而关闭。
