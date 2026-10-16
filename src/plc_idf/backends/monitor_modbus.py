"""backends/monitor_modbus.py — 只读 Modbus 监测后端（薄封装）。

封装 wsl-tools/e521_monitor.py（严格只读监测客户端，65 项测试资产）：
本模块只做"组装 argv + subprocess 透传"，不重新实现任何 Modbus 逻辑。
安全：组装完成后过 safety.ensure_no_write_tokens 黑名单闸门再执行，
写语义记号在执行前即被拒绝；被封装脚本本身也硬编码只读（双保险）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..safety import ensure_no_write_tokens
from .base import MonitorBackend

#: 转发给 e521_monitor.py 的布尔开关（出现即加对应旗标）
_BOOL_FLAGS = {
    "once": "--once",
    "calibrate": "--calibrate",
    "with_bits": "--with-bits",
}

#: 转发的带值参数：CLI 侧键名 -> e521_monitor.py 的旗标
_VALUE_FLAGS = {
    "transport": "--transport",
    "host": "--host",
    "port": "--port",
    "device": "--device",
    "baud": "--baud",
    "unit_id": "--unit-id",
    "interval": "--interval",
    "timeout": "--timeout",
    "word_order": "--word-order",
    "jsonl": "--jsonl",
}


class MonitorModbusBackend(MonitorBackend):
    """e521_monitor.py 的 subprocess 薄封装（透传参数）。"""

    name = "monitor_modbus"

    def __init__(self, cfg, script: Path | None = None):
        self.cfg = cfg
        self.script = Path(script) if script else Path(
            cfg.paths["wsl_tools"]) / "e521_monitor.py"

    def build_command(self, opts: dict) -> list[str]:
        """组装 argv；未显式给出的参数用配置默认值补齐。"""
        merged = dict(self.cfg.monitor.to_dict())
        merged.update({k: v for k, v in opts.items() if v is not None})
        argv: list[str] = [sys.executable, str(self.script)]
        for key, flag in _VALUE_FLAGS.items():
            value = merged.get(key)
            if value is not None:
                argv += [flag, str(value)]
        for key, flag in _BOOL_FLAGS.items():
            if merged.get(key):
                argv.append(flag)
        # 键名兼容：允许调用方用 CLI 风格的额外参数（透传层）
        argv += [str(a) for a in opts.get("extra", [])]
        ensure_no_write_tokens(argv)   # 只读闸门：写语义记号一律拒绝
        return argv

    def run(self, opts: dict, timeout: float | None = None) -> int:
        """前台运行监测（stdout/stderr 透传），返回退出码。"""
        argv = self.build_command(opts)
        try:
            return subprocess.run(argv, timeout=timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            print(f"[plcidf] monitor 子进程超过 {timeout}s 硬超时，已终止",
                  file=sys.stderr)
            return 124
