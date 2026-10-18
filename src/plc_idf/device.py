"""device.py — USB 设备层：usbipd.exe 封装与 /dev/ttyACM* 探测。

职责：
    - list_windows_usb(): usbipd list（interop 只读）→ 结构化设备表
    - find_tty_devices():  WSL 侧 CDC 串口探测（/dev/ttyACM* 等）
    - attach/detach:       usbipd bind/attach/detach（真机操作，双因子门禁内）
    - attach/detach 前后校验设备状态与串口出现/消失，校验不过视为失败

约束：
    - 所有 usbipd 调用走 interop.run_command（超时 + 重试上限）
    - list 永远只读；attach/detach 必须传入 granted 的 AuthorizationDecision
"""

from __future__ import annotations

import glob
import re
import time
from dataclasses import dataclass

from .interop import InteropError, InteropResult, RetryPolicy, require_ok, run_command
from .safety import AuthorizationDecision, EvidenceLogger, SafetyError

#: usbipd list 数据行：BUSID VID:PID 描述... 状态
_ROW_RE = re.compile(
    r"^(?P<busid>\d+(?:-\d+)+)\s+(?P<vidpid>[0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+")

#: 视为"已挂载进 WSL"的状态
STATE_ATTACHED = "attached"
STATE_SHARED = "shared"
STATE_NOT_SHARED = "not shared"


@dataclass(frozen=True)
class DeviceEntry:
    """usbipd list 的一行。"""

    busid: str
    vidpid: str
    description: str
    state: str

    @property
    def is_attached(self) -> bool:
        return self.state.lower() == STATE_ATTACHED

    @property
    def is_shared(self) -> bool:
        return self.state.lower() == STATE_SHARED

    @property
    def is_bound(self) -> bool:
        """是否已 bind（shared 或 attached 都算已共享给 usbipd）。"""
        return self.state.lower() in (STATE_SHARED, STATE_ATTACHED)


def parse_usbipd_list(text: str) -> list[DeviceEntry]:
    """解析 `usbipd list` 的 stdout（中英文描述、任意列宽均可）。

    只认 "BUSID VID:PID ..." 形态的数据行（Connected 区）；Persisted 区
    的 GUID 行与表头/空行全部跳过。描述与状态按 2+ 空格分列。
    """
    entries: list[DeviceEntry] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _ROW_RE.match(line)
        if not m:
            continue
        fields = [f for f in re.split(r"\s{2,}", line.strip()) if f]
        # fields 形如 [busid, vid:pid, 描述(可含单空格), 状态]
        if len(fields) < 4:
            # 少数极端对齐下描述与状态可能被并进一列：状态取最后一个已知词
            tail = fields[-1]
            known = {"Attached", "Shared", "Not shared", "Error", "Detached"}
            state = next((k for k in known if tail.endswith(k)), "")
            desc = tail[: len(tail) - len(state)].strip() if state else tail
            fields = [fields[0], fields[1], desc, state]
        desc = " ".join(fields[2:-1]).strip()
        entries.append(DeviceEntry(
            busid=fields[0], vidpid=m.group("vidpid").lower(),
            description=desc, state=fields[-1].strip()))
    return entries


def find_tty_devices(pattern: str = "/dev/ttyACM*") -> list[str]:
    """探测 WSL 侧串口设备（默认 CDC ACM，兼容 ttyUSB）。"""
    return sorted(glob.glob(pattern)) or sorted(glob.glob("/dev/ttyUSB*"))


# =====================【usbipd 交互】=====================

def _usbipd_argv(cfg) -> list[str]:
    return [cfg.device["usbipd_exe"]]


def list_windows_usb(cfg, runner=None,
                     policy: RetryPolicy | None = None) -> list[DeviceEntry]:
    """usbipd list（只读）。usbipd 不可用/超时报 InteropError，由上层降级。"""
    argv = _usbipd_argv(cfg) + ["list"]
    result = run_command(argv, policy=policy or RetryPolicy.from_config(cfg),
                         runner=runner)
    if not result.ok:
        raise InteropError(
            f"usbipd list 失败: {result.summary()} stderr={result.stderr.strip()[:160]!r}")
    return parse_usbipd_list(result.stdout)


def find_plc_entry(cfg, entries: list[DeviceEntry]) -> DeviceEntry | None:
    """BUSID 与 VID:PID 必须同时匹配；重复 BUSID 的不明设备表拒绝使用。

    VID:PID 只识别产品类型，不能在配置 BUSID 消失时自动选择另一台同型号
    PLC。BUSID 是本次连接的位置身份，不代表设备的永久序列号。
    """
    busid = str(cfg.device["busid"]).strip()
    vidpid = str(cfg.device["vidpid"]).strip().lower()
    if not re.fullmatch(r"\d+(?:-\d+)+", busid) or not re.fullmatch(
            r"[0-9a-f]{4}:[0-9a-f]{4}", vidpid):
        raise InteropError("PLC 配置身份无效：需要明确 BUSID 和 VID:PID")
    candidates = [entry for entry in entries if entry.busid == busid]
    if len(candidates) > 1:
        raise InteropError(f"PLC 设备身份有歧义：BUSID {busid} 出现 {len(candidates)} 次")
    if candidates and candidates[0].vidpid.lower() == vidpid:
        return candidates[0]
    return None


@dataclass
class DeviceStatus:
    """devices 命令的在线状态汇总。"""

    usbipd_ok: bool
    entries: list = None  # type: ignore[assignment]  # list[DeviceEntry]
    plc: DeviceEntry | None = None
    tty: list = None  # type: ignore[assignment]  # list[str]
    error: str | None = None

    def __post_init__(self):
        if self.entries is None:
            self.entries = []
        if self.tty is None:
            self.tty = []


def probe_devices(cfg, runner=None) -> DeviceStatus:
    """只读汇总：usbipd 设备表 + PLC 条目 + WSL 串口。interop 失败可降级。"""
    tty = find_tty_devices(cfg.device["tty_glob"])
    try:
        entries = list_windows_usb(cfg, runner=runner)
    except (InteropError, OSError) as exc:      # usbipd 缺失/超时/失败都降级
        return DeviceStatus(usbipd_ok=False, entries=[], plc=None,
                            tty=tty, error=str(exc))
    plc = find_plc_entry(cfg, entries)
    return DeviceStatus(usbipd_ok=True, entries=entries, plc=plc, tty=tty)


def render_devices_table(status: DeviceStatus, busid_expected: str = "") -> str:
    """渲染设备在线状态表（终端可读）。"""
    lines = ["=== plcidf devices — USB 设备在线状态"]
    if status.usbipd_ok:
        lines.append(f"--- usbipd list: {len(status.entries)} 台设备（Windows 侧）")
        lines.append(f"{'BUSID':<8}{'VID:PID':<12}{'STATE':<12}描述")
        for e in status.entries:
            mark = " <= PLC(配置)" if busid_expected and e.busid == busid_expected else ""
            lines.append(f"{e.busid:<8}{e.vidpid:<12}{e.state:<12}{e.description}{mark}")
    else:
        lines.append(f"--- usbipd 不可用: {status.error}")
        lines.append("--- （仅报告 WSL 侧串口；不阻塞只读链路）")
    if status.plc is not None:
        state = "已挂载(Attached)" if status.plc.is_attached else status.plc.state
        lines.append(f"--- PLC 设备: BUSID {status.plc.busid} {status.plc.vidpid} "
                     f"{status.plc.description} -> {state}")
    else:
        lines.append(f"--- PLC 设备: 未找到（期望 BUSID {busid_expected or '6-4'}"
                     f" / H5U）")
    if status.tty:
        lines.append(f"--- WSL 串口: {' '.join(status.tty)}  -> 在线")
    else:
        lines.append("--- WSL 串口: 无 /dev/ttyACM*|ttyUSB* -> 离线")
    return "\n".join(lines)


# =====================【attach / detach（真机操作，门禁内）】=====================

def _wait_until(predicate, timeout_s: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _run_usbipd(cfg, args: list[str], policy: RetryPolicy,
                runner=None, retryable: bool = False) -> InteropResult:
    return run_command(_usbipd_argv(cfg) + args, policy=policy,
                       runner=runner, retryable=retryable)


def attach_device(cfg, decision: AuthorizationDecision,
                  evidence: EvidenceLogger | None = None,
                  runner=None) -> InteropResult:
    """把 PLC USB 设备挂载进 WSL（bind 如未共享 + attach --wsl）。

    门禁：decision 必须是 granted 的双因子裁决，否则 SafetyError。
    前置校验：设备存在且未 Attached；后置校验：状态变 Attached 且
    串口出现（在 attach_settle_s 内），任一不满足即抛 InteropError。
    """
    if not decision.granted:
        raise SafetyError(f"usbipd attach 被门禁拒绝: {decision.reason_text}")
    policy = RetryPolicy.from_config(cfg)
    settle = float(cfg.device["attach_settle_s"])

    pre = list_windows_usb(cfg, runner=runner, policy=policy)
    plc = find_plc_entry(cfg, pre)
    if plc is None:
        raise InteropError(
            f"attach 前置校验失败：找不到 PLC 设备（BUSID {cfg.device['busid']}）")
    if plc.is_attached:
        raise InteropError(f"attach 前置校验失败：{plc.busid} 已是 Attached，无需重复挂载")

    results: list[InteropResult] = []
    if not plc.is_bound:
        results.append(_run_usbipd(cfg, ["bind", "--busid", plc.busid],
                                   policy, runner))
        require_ok(results[-1], f"usbipd bind {plc.busid}")
    results.append(_run_usbipd(cfg, ["attach", "--wsl", "--busid", plc.busid],
                               policy, runner))
    require_ok(results[-1], f"usbipd attach {plc.busid}")

    def _verify() -> bool:
        entries = list_windows_usb(cfg, runner=runner, policy=policy)
        got = find_plc_entry(cfg, entries)
        return bool(got and got.is_attached and find_tty_devices(cfg.device["tty_glob"]))

    if not _wait_until(_verify, settle):
        raise InteropError(
            f"attach 后置校验失败：{plc.busid} 未在 {settle}s 内变为"
            f" Attached 且串口出现")
    if evidence:
        evidence.log("device.attach", params={"busid": plc.busid},
                     result={"ok": True, "steps": [r.summary(60) for r in results]})
    return results[-1]


def detach_device(cfg, decision: AuthorizationDecision,
                  evidence: EvidenceLogger | None = None,
                  runner=None) -> InteropResult:
    """把 PLC USB 设备从 WSL 卸载（detach --wsl；前后校验状态与串口消失）。"""
    if not decision.granted:
        raise SafetyError(f"usbipd detach 被门禁拒绝: {decision.reason_text}")
    policy = RetryPolicy.from_config(cfg)
    settle = float(cfg.device["attach_settle_s"])

    pre = list_windows_usb(cfg, runner=runner, policy=policy)
    plc = find_plc_entry(cfg, pre)
    if plc is None:
        raise InteropError(f"detach 前置校验失败：找不到 PLC 设备（BUSID {cfg.device['busid']}）")
    if not plc.is_attached:
        raise InteropError(f"detach 前置校验失败：{plc.busid} 未挂载（state={plc.state}）")

    result = _run_usbipd(cfg, ["detach", "--wsl", "--busid", plc.busid],
                         policy, runner)
    require_ok(result, f"usbipd detach {plc.busid}")

    def _verify() -> bool:
        entries = list_windows_usb(cfg, runner=runner, policy=policy)
        got = find_plc_entry(cfg, entries)
        return bool(got is not None and not got.is_attached
                    and not find_tty_devices(cfg.device["tty_glob"]))

    if not _wait_until(_verify, settle):
        raise InteropError(f"detach 后置校验失败：{plc.busid} 未在 {settle}s 内"
                           f" 退出 Attached 或串口仍在")
    if evidence:
        evidence.log("device.detach", params={"busid": plc.busid},
                     result={"ok": True, "steps": [result.summary(60)]})
    return result
