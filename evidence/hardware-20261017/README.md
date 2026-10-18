# 2026-10-17 Easy521 现场证据索引

结论与限制以 [现场报告](../../docs/management/LIVE_VALIDATION_2026-10-17.md) 为准。这里保留成功、失败及取消操作的原始记录，不能把任意一个 returncode=0 当作完整验收。

- 设备：`cli-devices.json`、`2026-10-17T102619.030172_0000-plc-info-query.json`。
- 网络：`host-interfaces.json`、`network-final.json`、`network-pre-submit.json`。最后一次仍为物理链路断开。
- 最终 CLI 编译：`cli-build-negative-final-guard.json`（退出 1）、`cli-build-positive-final-guard.json`（退出 0）、`build-manifest-final-guard.json`。较早的 `*-retry.json` 是修复适配后人工发起的独立测试记录，不代表工具自动重放状态操作。
- 工程保护：`original-project-snapshot.json` 与 `original-project-final-verification.json`；原工程 120 文件无变化。
- 设备上载：`2026-10-17T104527.384689_0000-backup-transfer-log.json`、`usb-upload-backup-manifest.json`、`2026-10-17T104528.600852_0000-backup-uploaded-main.json`。完整 62 文件备份及 ZIP 留在 Windows 本机，未将整个备份上传 GitHub；没有执行恢复演练。
- 中间过程：带时间戳的 JSON、截图及 OCR 结果。包括标题适配失败、读取误触替换后的恢复和取消上载流程，不删去这些失败记录。
- `pre-negative-MAIN.*` 是负向实验前的旧产物快照，不是本次正向构建产物；`known-good-main.st` 与 `invalid-main.st` 为输入样本。

本目录有设备上载备份的日志与清单；没有真实程序下载、RUN/STOP、Modbus 网络监测或固件升级的验收记录。日志/窗口读取适配依赖已验证的 AutoShop 版本。
