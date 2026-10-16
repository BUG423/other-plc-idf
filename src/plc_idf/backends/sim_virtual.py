"""backends/sim_virtual.py — 虚拟 Easy521 从站后端（薄封装）。

封装 wsl-tools/virtual_easy521.py（软件闭环从站资产）：
    - build_command(): 组装 argv（参数与脚本一一对应）
    - wait_ready():    阻塞读 stdout 直到脚本打印 "READY"（超时抛 TimeoutError）
    - run_with_monitor(): 一键闭环——起从站 -> 等就绪 -> 跑一轮只读监测 -> 收尾
        这是 `plcidf sim --with-monitor --once` 的执行体，演示与冒烟两用。
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from ..safety import EvidenceLogger


def find_free_port() -> int:
    """向内核要一个空闲回环端口（起从站前用，避免 1502 被占）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SimVirtualBackend:
    """virtual_easy521.py 的 subprocess 薄封装。"""

    name = "sim_virtual"

    def __init__(self, cfg, script: Path | None = None):
        self.cfg = cfg
        self.script = Path(script) if script else Path(
            cfg.paths["wsl_tools"]) / "virtual_easy521.py"

    # ---------- argv 组装 ----------

    def build_command(self, opts: dict) -> list[str]:
        """组装从站 argv；未显式给出的参数用配置默认值补齐。"""
        merged = dict(self.cfg.sim.to_dict())
        merged.update({k: v for k, v in opts.items() if v is not None})
        # -u：非缓冲 stdout——管道下 READY 探测必需（与 wsl-tools e2e 测试同法）
        argv: list[str] = [sys.executable, "-u", str(self.script)]
        if merged.get("host"):
            argv += ["--host", str(merged["host"])]
        if merged.get("port"):
            argv += ["--port", str(merged["port"])]
        if merged.get("scenario"):
            argv += ["--scenario", str(merged["scenario"])]
        if merged.get("word_order"):
            argv += ["--word-order", str(merged["word_order"])]
        if merged.get("tick"):
            argv += ["--tick", str(merged["tick"])]
        if merged.get("hold_dip"):
            argv += ["--hold-dip", str(merged["hold_dip"])]
        if merged.get("log"):
            argv += ["--log", str(merged["log"])]
        if merged.get("quiet"):
            argv.append("--quiet")
        return argv

    # ---------- 进程编排 ----------

    def wait_ready(self, proc: subprocess.Popen, timeout: float) -> str:
        """读 stdout 直到 READY 行；超时/提前退出杀进程并抛 TimeoutError。

        实现说明：必须用后台读线程而非 selectors——text=True 的
        TextIOWrapper 会预读，后续行躺在用户态缓冲里，底层 fd 永远不再
        可读，select 会假死（wsl-tools e2e 用读线程，同因）。
        返回已消耗的头部输出（诊断用）。stderr 合并在 stdout 里（Popen 侧）。
        """
        import queue as queue_mod
        import threading
        import time

        lines_q: queue_mod.Queue = queue_mod.Queue()

        def _pump() -> None:
            try:
                for line in proc.stdout:
                    lines_q.put(line)
            except (ValueError, OSError):
                pass
            finally:
                lines_q.put(None)                  # EOF 哨兵

        threading.Thread(target=_pump, daemon=True, name="sim-stdout").start()
        deadline = time.monotonic() + timeout
        head: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise TimeoutError(f"等待虚拟从站 READY 超过 {timeout}s，"
                                   f"输出头部:\n{''.join(head)[-800:]}")
            try:
                line = lines_q.get(timeout=min(remaining, 0.2))
            except queue_mod.Empty:
                continue
            if line is None:                       # EOF：进程提前退出
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise TimeoutError(
                    f"虚拟从站提前退出 rc={proc.returncode}，输出头部:\n"
                    f"{''.join(head)[-800:]}")
            head.append(line)
            if "READY" in line:
                return "".join(head)

    def run_with_monitor(self, sim_opts: dict, monitor_opts: dict,
                         evidence: EvidenceLogger | None = None) -> dict:
        """一键闭环：起从站 -> 等就绪 -> 一轮只读监测 -> 干净收尾。

        返回 {sim_port, monitor_rc, monitor_output, ready_head}；
        任何一步失败都保证从站进程被终止（finally 收口）。
        """
        # 端口未显式指定时取空闲端口，避免与常驻从站/真机端口冲突
        port = sim_opts.get("port")
        if not port:
            port = find_free_port()
            sim_opts = {**sim_opts, "port": port}
        monitor_opts = {**monitor_opts, "host": "127.0.0.1", "port": port,
                        "once": bool(monitor_opts.get("once", True))}
        ready_timeout = float(sim_opts.get("ready_timeout")
                              or self.cfg.sim["ready_timeout"])
        exec_timeout = float(sim_opts.get("monitor_exec_timeout")
                             or self.cfg.sim["monitor_exec_timeout"])

        sim_argv = self.build_command(sim_opts)
        monitor = MonitorBackendRunner(self.cfg)   # 延迟导入避免循环
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(sim_argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            if evidence:
                evidence.log("sim.start", params={"argv": sim_argv},
                             result={"port": port})
            head = self.wait_ready(proc, ready_timeout)
            monitor_argv = monitor.build(monitor_opts)
            if evidence:
                evidence.log("monitor.run", params={"argv": monitor_argv},
                             result={"mode": "with-sim", "port": port})
            completed = subprocess.run(monitor_argv, capture_output=True,
                                       text=True, timeout=exec_timeout, check=False)
            output = completed.stdout + completed.stderr
            if evidence:
                evidence.log("sim.loop.done",
                             params={"port": port},
                             result={"monitor_rc": completed.returncode})
            return {"sim_port": port, "monitor_rc": completed.returncode,
                    "monitor_output": output, "ready_head": head,
                    "sim_argv": sim_argv, "monitor_argv": monitor_argv}
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if evidence:
                    evidence.log("sim.stop", params={"port": port},
                                 result={"terminated": True})


class MonitorBackendRunner:
    """sim 闭环里用的最小监测 argv 组装器（避免导入环）。"""

    def __init__(self, cfg):
        from .monitor_modbus import MonitorModbusBackend
        self._backend = MonitorModbusBackend(cfg)

    def build(self, opts: dict) -> list[str]:
        return self._backend.build_command(opts)
