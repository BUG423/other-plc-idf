"""safety.py — plcidf 安全硬约束的落地点（门禁 + 只读强制 + 证据日志）。

三条硬约束，全部收口在本模块：

1. 只读强制
    monitor 数据面硬编码只读：只允许 FC01/02/03/04 读功能码
    （assert_readonly_fc / ensure_no_write_tokens），任何代码路径想把
    写功能码塞进命令行都会在执行前被拒绝。

2. 双因子授权门禁
    build/flash 真执行必须同时满足：
        配置 bridge.enabled = true（项目级开关，写在 .plcidf.toml）
        命令行显式 --i-have-authorized（操作者当次显式声明）
    缺一即拒（check_bridge_authorization -> AuthorizationDecision），
    拒绝时打印具体缺哪一项。

3. 证据日志
    所有真机操作（含 dry-run、interop 每一步、设备 attach/detach、
    监测/仿真进程的启停）写 JSONL 证据日志（EvidenceLogger）。
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# =====================【1. 只读强制】=====================

#: 读功能码白名单——monitor 链路唯一允许的功能码集合
READ_FUNCTION_CODES = frozenset({1, 2, 3, 4})

#: 写功能码黑名单——出现即 ReadOnlyViolation
WRITE_FUNCTION_CODES = frozenset({5, 6, 15, 16, 22, 23})

#: 命令行里禁止出现的写语义记号（子命令透传层的黑名单闸门）
FORBIDDEN_ARG_TOKENS: tuple[str, ...] = (
    "--fc-write", "--write", "write_coil", "write_coils", "write_register",
    "write_registers", "write_bit", "write_bits", "FC05", "FC06", "FC15",
    "FC16", "FC22", "FC23",
)


class SafetyError(RuntimeError):
    """安全门禁拒绝。exit code 约定映射 2。"""


class ReadOnlyViolation(SafetyError):
    """试图引入写功能码/写语义。"""


def is_readonly_fc(func_code: int) -> bool:
    """功能码是否在只读白名单内。"""
    return int(func_code) in READ_FUNCTION_CODES


def assert_readonly_fc(func_code: int) -> None:
    """断言功能码只读；写功能码一律抛 ReadOnlyViolation（含功能码号）。"""
    fc = int(func_code)
    if fc in WRITE_FUNCTION_CODES:
        raise ReadOnlyViolation(
            f"只读边界违规：FC{fc:02d} 是写功能码，本工具链数据面只允许 "
            f"FC01/02/03/04（读）。写功能码集合 {sorted(WRITE_FUNCTION_CODES)} 全部禁止。")
    if not is_readonly_fc(fc):
        raise ReadOnlyViolation(f"未知功能码 FC{fc:02d}，不在只读白名单 {sorted(READ_FUNCTION_CODES)} 内。")


def ensure_no_write_tokens(argv: list[str] | tuple[str, ...]) -> None:
    """扫描将要执行的命令行参数，出现写语义记号即拒绝（透传层黑名单）。"""
    for token in argv:
        for bad in FORBIDDEN_ARG_TOKENS:
            if bad.lower() in str(token).lower():
                raise ReadOnlyViolation(
                    f"只读边界违规：监测/仿真命令行不允许出现写语义记号 {bad!r}"
                    f"（完整参数: {list(argv)!r}）")


# =====================【2. 双因子授权门禁】=====================

@dataclass
class AuthorizationDecision:
    """门禁裁决：granted=False 时 reasons 逐条说明缺什么。"""

    granted: bool
    reasons: list[str] = field(default_factory=list)
    factors: dict = field(default_factory=dict)

    @property
    def reason_text(self) -> str:
        return "；".join(self.reasons)


def check_bridge_authorization(cfg, flag: bool) -> AuthorizationDecision:
    """bridge 真执行的双因子门禁：配置 bridge.enabled=true 且显式授权旗标。

    两个因子独立判定并给出人话原因；只有两者同时成立才 granted=True。
    """
    reasons: list[str] = []
    cfg_enabled = bool(cfg.bridge.get("enabled", False))
    if not cfg_enabled:
        reasons.append(
            "配置因子不满足：.plcidf.toml 中 bridge.enabled=false"
            "（项目级开关，默认关闭）")
    if not flag:
        reasons.append(
            "授权因子不满足：命令行缺少 --i-have-authorized"
            "（操作者当次显式授权声明）")
    return AuthorizationDecision(
        granted=not reasons, reasons=reasons,
        factors={"config_bridge_enabled": cfg_enabled, "cli_flag": bool(flag)})


def require_authorization(decision: AuthorizationDecision, action: str) -> None:
    """门禁不通过即抛 SafetyError（CLI 捕获后打印原因并返回码 2）。"""
    if decision.granted:
        return
    raise SafetyError(
        f"已拒绝{action}：真机操作需要双因子授权，缺一即拒。原因："
        + "；".join(decision.reasons)
        + "。解锁方式：在 .plcidf.toml 设 bridge.enabled=true，"
          "并在命令行同时显式加 --i-have-authorized。")


# =====================【3. 证据日志】=====================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EvidenceLogger:
    """JSONL 证据日志：每行一个事件 {ts, command, params, result, ...}。

    尽力而为：磁盘异常只告警不抛（证据缺失不该炸掉正在执行的真机操作，
    但要在 stderr 留痕）。线程安全（一把锁，CLI 单线程场景冗余无害）。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._warned = False

    def log(self, command: str, params: dict | None = None,
            result: dict | None = None, **extra) -> dict:
        record = {
            "ts": _now_iso(),
            "command": command,
            "params": params or {},
            "result": result or {},
        }
        record.update(extra)
        line = json.dumps(record, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            if not self._warned:
                self._warned = True
                print(f"[plcidf] 证据日志写入失败（{self.path}）: {exc}",
                      file=sys.stderr)
        return record

    def read_all(self) -> list[dict]:
        """读回全部事件（测试与审计用；文件不存在返回空表）。"""
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def make_evidence_logger(cfg) -> EvidenceLogger:
    """按配置构造证据日志器（路径相对项目根）。"""
    from .config import evidence_path
    return EvidenceLogger(evidence_path(cfg))
