"""plc_idf — PLC-IDF（曾用名 py2plc）的 BESS Easy521 工具链命令行包。

命令行入口为 `plcidf`（旧名 `py2plc` 保留为等价别名）。子命令：
    build    桥式 AutoShop 后端编译管线（默认 dry-run，真执行需双因子授权）
    flash    桥式 AutoShop 后端下载管线（默认 dry-run，真执行需双因子授权）
    monitor  只读 Modbus 监测（封装 wsl-tools/e521_monitor.py，硬编码只读）
    sim      虚拟 Easy521 从站（封装 wsl-tools/virtual_easy521.py），
             --with-monitor 一键起"从站+监测"闭环演示
    devices  USB 设备在线状态表（usbipd list 只读 interop + /dev/ttyACM* 探测）

安全边界（详见 src/plc_idf/safety.py）：
    monitor/sim 数据面只读（FC01/02/03/04）；build/flash 默认 dry-run，
    真执行必须同时满足配置 bridge.enabled=true 与命令行 --i-have-authorized。
"""

__version__ = "0.2.0"
