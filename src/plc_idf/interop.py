"""interop.py — WSL→Windows interop 子进程统一封装（超时 + 重试上限）。

所有跨入 Windows 侧的调用（usbipd.exe / powershell.exe）都必须走
run_command()，保证：
    1) 每次调用有超时（默认取配置 net.timeout_s）；
    2) 失败重试有上限（默认取配置 net.retries，仅对"可能瞬时失败"的错误重试）；
    3) 结果结构化返回，供证据日志落盘。

本模块本身不关心业务语义，也不知道授权与否——授权门禁在 safety.py。
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Windows PowerShell 的 WSL 侧可见路径（Windows 下同样是固定系统路径）
POWERSHELL_WSL_PATH = "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"
POWERSHELL_WIN_PATH = r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"

#: USBIPD 的默认安装位置（usbipd-win 4.x/5.x MSI 均落在此处）
USBIPD_WSL_PATH = "/mnt/c/Program Files/usbipd-win/usbipd.exe"


class InteropError(RuntimeError):
    """interop 调用在重试上限内仍未成功。"""


@dataclass
class InteropResult:
    """一次 interop 调用的结构化结果。"""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    attempts: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def summary(self, width: int = 120) -> str:
        """单行摘要（证据日志/终端输出用）。"""
        head = (self.stdout or "").strip().replace("\n", " ⏎ ")
        if len(head) > width:
            head = head[:width] + "…"
        return f"rc={self.returncode} attempts={self.attempts} out={head!r}"


@dataclass
class RetryPolicy:
    """超时与重试上限（约束：所有网络/设备操作必须有界）。"""

    timeout_s: float = 10.0
    retries: int = 2              # 首次失败后最多再试次数（总尝试 = retries + 1）
    backoff_s: float = 1.0

    @classmethod
    def from_config(cls, cfg) -> "RetryPolicy":
        net = cfg.net
        return cls(timeout_s=float(net.get("timeout_s", 10.0)),
                   retries=int(net.get("retries", 2)),
                   backoff_s=float(net.get("retry_backoff_s", 1.0)))


#: 测试注入点类型：与 subprocess.run 同形签名
Runner = object


def run_command(argv: list[str],
                policy: RetryPolicy | None = None,
                runner: Runner | None = None,
                retryable: bool = True,
                cwd: Path | None = None) -> InteropResult:
    """带超时/重试地执行一条命令，返回 InteropResult。

    runner 供测试注入（默认 subprocess.run）；retryable=False 时不重试
    （用于一次性动作，如 attach/detach 的执行步）。
    超时与被kill统一按 returncode=124 归一（类 GNU timeout 语义）。
    """
    policy = policy or RetryPolicy()
    runner = runner if runner is not None else _default_runner
    attempts_allowed = policy.retries + 1
    last: InteropResult | None = None
    for attempt in range(1, attempts_allowed + 1):
        try:
            proc = runner(argv, timeout=policy.timeout_s, cwd=str(cwd) if cwd else None)
            last = InteropResult(argv=list(argv), returncode=proc.returncode,
                                 stdout=proc.stdout or "", stderr=proc.stderr or "",
                                 attempts=attempt)
        except subprocess.TimeoutExpired as exc:
            last = InteropResult(
                argv=list(argv), returncode=124, timed_out=True, attempts=attempt,
                stdout=(exc.stdout or b"").decode(errors="replace") if isinstance(
                    exc.stdout, bytes) else (exc.stdout or ""),
                stderr=(exc.stderr or b"").decode(errors="replace") if isinstance(
                    exc.stderr, bytes) else (exc.stderr or ""),
            )
        except FileNotFoundError as exc:
            # 二进制不存在属永久错误：不重试，直接抛（调用方降级/报错）
            raise InteropError(f"interop 可执行文件不存在: {argv[0]} ({exc})") from exc
        if last.ok:
            return last
        if not retryable or attempt >= attempts_allowed:
            return last
        time.sleep(policy.backoff_s)
    assert last is not None  # 循环必经至少一次
    return last


def _default_runner(argv, timeout, cwd=None):
    # PowerShell can emit an ANSI error before our UTF-8 setup runs (for
    # example, parameter validation). Decode after capturing both streams so a
    # completed, possibly mutating command never becomes a Unicode traceback.
    result = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=cwd)
    def decode(data):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="backslashreplace")
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return subprocess.CompletedProcess(result.args, result.returncode,
                                       decode(result.stdout), decode(result.stderr))


def require_ok(result: InteropResult, what: str) -> InteropResult:
    """断言 interop 成功，否则抛 InteropError（带上下文摘要）。"""
    if not result.ok:
        raise InteropError(
            f"{what} 失败（{result.summary()}）stderr={result.stderr.strip()[:200]!r}")
    return result


def to_windows_unc(path: Path, distro: str | None = None) -> str:
    """把 WSL 侧路径转成 Windows 可访问的 \\\\wsl.localhost\\ UNC 路径。"""
    import os
    distro = distro or os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
    win = str(path.resolve()).replace("/", "\\")
    return f"\\\\wsl.localhost\\{distro}{win}"
