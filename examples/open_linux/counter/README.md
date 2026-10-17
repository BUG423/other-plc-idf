# 本地 Linux 完整 ST 示例

`main.st` 是唯一维护的应用模板。它声明 MAIN、20 ms 周期任务以及配置/资源：每次真实扫描 COUNTER 加 1，每 10 次翻转 HEARTBEAT。无物理 I/O。

编译需要固定 MatIEC/GCC，不通过 Modbus 模拟程序行为。安装、独立 API、运行范围与实际证据见 [开放 Linux ST 后端](../../../docs/architecture/OPEN_LINUX_RUNTIME.md)。这证明本地 Linux ST 执行，不证明 Easy521 固件或工业实时性。
