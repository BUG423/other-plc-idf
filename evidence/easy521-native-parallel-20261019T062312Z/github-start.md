用户要求并行继续 Easy521 的纯 Linux 全流程，已启动三条 AI 子代理工作线，由统筹独占集成、Git/GitHub 与设备操作，全部在 main 共享文件范围协作：

1. **Linux 原生 USB**：实现固定 GCM 状态/内存读取的 ctypes + libusb-1.0 后端，保留 Windows 默认路径；preview 不加载库或枚举，发送一次，不重放，超时保存未知结果。正在离线/mock 验证。
2. **完整应用配置**：离线核验 FOID 头、模块版本来源和 payload 关系，形成严格解析工具。没有凭空生成缺失字段或宣布完整应用包可下载。
3. **ST→ARM ABI**：利用本机 Linux LLVM，研究限定 DINT 自增探针的源码→IR→对象，独立对照厂商调用约定；不以局部 ABI 相符替代 runtime/下载验收。

统筹本次已完成主机前置检查：唯一 0471:a531 在 Windows BUSID 7-2、Not shared，AutoShop 未运行；WSL libusb 存在。随后通过本发行版 root 正常加载 vhci-hcd，原来缺失的 USB sysfs/devfs 已出现，只有两个 root hubs。该动作只改变主机内核模块，未发送 PLC 请求，未更改输出/CPU/应用。

设备仍由 Windows 持有；当前 Windows 会话非管理员，尚未执行 bind/attach。原生真机读取与原生完整应用下装均未通过，软件成果、主机准备和硬件验收分别报告。阶段完成后补代码提交、必要验证、精确证据与剩余缺口，不关闭 #10。
