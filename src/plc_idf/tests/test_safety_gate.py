"""安全门禁：双因子授权（缺一即拒）、只读强制、证据日志、解锁后调用点断言。"""

import json

import pytest

from plc_idf import cli
from plc_idf.backends import bridge_autoshop as bridge_mod
from plc_idf.config import load_config
from plc_idf.interop import InteropResult
from plc_idf.safety import (AuthorizationDecision, EvidenceLogger,
                           ReadOnlyViolation, SafetyError,
                           assert_readonly_fc, check_bridge_authorization,
                           ensure_no_write_tokens)

from .conftest import USBIPD_SAMPLE, USBIPD_SAMPLE_ONWIN, write_cfg

pytestmark = pytest.mark.usefixtures("clean_env")


# ---------- 双因子门禁矩阵 ----------

@pytest.mark.parametrize("cfg_enabled,flag,granted,n_reasons", [
    (False, False, False, 2),   # 双缺：两因子的原因都要讲清楚
    (True,  False, False, 1),   # 缺旗标
    (False, True,  False, 1),   # 缺配置开关
    (True,  True,  True,  0),   # 双因子齐 -> 放行
])
def test_two_factor_matrix(tmp_path, cfg_enabled, flag, granted, n_reasons):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                         extra=f"[bridge]\nenabled = {'true' if cfg_enabled else 'false'}\n")
    cfg = load_config(str(cfg_path))
    decision = check_bridge_authorization(cfg, flag)
    assert decision.granted is granted
    assert len(decision.reasons) == n_reasons


def test_refusal_reasons_name_missing_factor(tmp_path):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl")
    cfg = load_config(str(cfg_path))
    reasons = check_bridge_authorization(cfg, False).reasons
    assert any("bridge.enabled" in r for r in reasons)
    assert any("--i-have-authorized" in r for r in reasons)


def test_require_authorization_raises_with_reasons(tmp_path):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl")
    decision = check_bridge_authorization(load_config(str(cfg_path)), False)
    with pytest.raises(SafetyError) as exc:
        from plc_idf.safety import require_authorization
        require_authorization(decision, "build 真执行")
    assert "缺一即拒" in str(exc.value)


# ---------- CLI 层：缺一即拒 ----------

def test_cli_execute_refused_without_flag(tmp_cfg, capsys, monkeypatch):
    """有 --execute、缺旗标：拒绝且打印配置因子原因；不执行任何 interop。"""
    monkeypatch.setattr(bridge_mod, "run_command",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("未解锁不允许触达 interop 调用点")))
    rc = cli.main(["build", "--execute", "--config", str(tmp_cfg)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--i-have-authorized" in err
    assert "bridge.enabled" in err          # 该配置默认 disabled，两个原因都给


def test_cli_execute_refused_without_config(tmp_cfg, capsys, monkeypatch):
    """有旗标、配置 bridge.enabled=false：仍拒绝（配置因子缺失）。"""
    monkeypatch.setattr(bridge_mod, "run_command",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("未解锁不允许触达 interop 调用点")))
    for sub in ("build", "flash"):
        rc = cli.main([sub, "--execute", "--i-have-authorized",
                       "--config", str(tmp_cfg)])
        assert rc == 2
        assert "bridge.enabled" in capsys.readouterr().err


# ---------- CLI 层：双因子齐 -> 放行到"已解锁"（调用点用 monkeypatch 拦截） ----------

class _FakeRunner:
    """拦截 bridge 的全部 interop 调用：usbipd list 给样本，GUI 步给假成功。

    返回 InteropResult（与真 run_command 同契约），供 _verify_plc_online 判定。
    """

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout=None, cwd=None, **_kw):
        self.calls.append(list(argv))
        stdout = (USBIPD_SAMPLE_ONWIN if argv[-1] == "list" else "GUI-ok")
        return InteropResult(argv=list(argv), returncode=0, stdout=stdout,
                             stderr="", attempts=1)


@pytest.mark.parametrize("sub", ["build", "flash"])
def test_unlocked_reaches_call_point_but_no_real_exec(tmp_cfg_unlocked, capsys,
                                                      monkeypatch, sub):
    """已解锁路径：门禁放行、真调用点被触达（fake runner 记录到调用链 argv），
    但测试拦截之下不会发生任何真机操作；证据日志逐步落盘。"""
    fake = _FakeRunner()
    monkeypatch.setattr(bridge_mod, "run_command", fake)

    rc = cli.main([sub, "--execute", "--i-have-authorized",
                   "--config", str(tmp_cfg_unlocked)])
    assert rc == 1  # 调用点可达不代表编译/下载结果已经核验
    out = capsys.readouterr().out
    assert "门禁通过" in out
    assert "调用链已完成，结果未核验" in out

    # 调用点确已触达：至少一条 usbipd 校验 + 多条 powershell interop 步
    # （冷启动步是 powershell -Command 内联脚本，其余 GUI 步走 autoshop_ui.ps1 -File）
    assert any(a[-1] == "list" and "usbipd" in " ".join(a) for a in fake.calls)
    ps_calls = [a for a in fake.calls if a[-1] != "list"]
    assert ps_calls and all("powershell" in " ".join(a) for a in ps_calls)
    assert any("autoshop_ui.ps1" in " ".join(a) for a in ps_calls)

    # 证据日志：逐步 bridge.<sub>.step + 收尾 done
    ev_path = tmp_cfg_unlocked.parent / "evidence.jsonl"
    events = [json.loads(ln) for ln in ev_path.read_text(encoding="utf-8").splitlines()]
    commands = [e["command"] for e in events]
    assert f"bridge.{sub}.step" in commands
    assert f"bridge.{sub}.done" in commands
    done = next(e for e in events if e["command"] == f"bridge.{sub}.done")
    assert done["result"]["ok"] is False
    assert done["result"]["verification_status"] == "unverified"
    assert done["result"]["command_chain_completed"] is True
    step_event = next(e for e in events if e["command"] == f"bridge.{sub}.step")
    assert "argv" in step_event["params"] and "ts" in step_event


def test_unlocked_verify_aborts_when_plc_absent(tmp_path, clean_env, monkeypatch,
                                                capsys):
    """已解锁但 USB 设备完全缺席：verify 步必须中止且不触碰任何 GUI 步。"""
    monkeypatch.chdir(tmp_path)     # 项目根无所谓，配置全显式
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                         extra="[bridge]\nenabled = true\n")
    absent_sample = "\n".join(
        ln for ln in USBIPD_SAMPLE_ONWIN.splitlines()
        if not ln.startswith("6-4")) + "\n"
    calls: list[list[str]] = []

    def runner(argv, timeout=None, cwd=None, **_kw):
        calls.append(list(argv))
        stdout = absent_sample if argv[-1] == "list" else "GUI-ok"
        return InteropResult(argv=list(argv), returncode=0, stdout=stdout,
                             stderr="", attempts=1)

    monkeypatch.setattr(bridge_mod, "run_command", runner)
    rc = cli.main(["build", "--execute", "--i-have-authorized",
                   "--config", str(cfg_path)])
    assert rc == 2                                  # verify 失败按安全拒绝处理
    assert "前置校验失败" in capsys.readouterr().err
    assert all(a[-1] == "list" for a in calls)     # 只做了只读校验，没碰 GUI


def test_unlocked_verify_aborts_when_plc_still_in_wsl(tmp_path, clean_env,
                                                      monkeypatch, capsys):
    """已解锁但设备仍挂载在 WSL（Attached）：verify 必须拒绝并提示 detach。

    桥式 build/flash 由 Windows 侧 AutoShop 执行，设备挂在 WSL 时
    AutoShop 反而看不到它——这正是 USB 所有权互斥的机器可检查面。"""
    monkeypatch.chdir(tmp_path)
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                         extra="[bridge]\nenabled = true\n")
    calls: list[list[str]] = []

    def runner(argv, timeout=None, cwd=None, **_kw):
        calls.append(list(argv))
        stdout = USBIPD_SAMPLE if argv[-1] == "list" else "GUI-ok"
        return InteropResult(argv=list(argv), returncode=0, stdout=stdout,
                             stderr="", attempts=1)

    monkeypatch.setattr(bridge_mod, "run_command", runner)
    rc = cli.main(["build", "--execute", "--i-have-authorized",
                   "--config", str(cfg_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "前置校验失败" in err and "detach" in err
    assert all(a[-1] == "list" for a in calls)


# ---------- 只读强制 ----------

@pytest.mark.parametrize("fc", [1, 2, 3, 4])
def test_readonly_fc_allowed(fc):
    assert assert_readonly_fc(fc) is None


@pytest.mark.parametrize("fc", [5, 6, 15, 16, 22, 23, 0, 99])
def test_readonly_fc_rejects(fc):
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_fc(fc)


@pytest.mark.parametrize("token", ["--fc-write", "write_register", "--FC05",
                                   "write_coils"])
def test_ensure_no_write_tokens_rejects(token):
    with pytest.raises(ReadOnlyViolation):
        ensure_no_write_tokens(["e521_monitor.py", token])


def test_ensure_no_write_tokens_allows_reads():
    ensure_no_write_tokens(["e521_monitor.py", "--once", "--calibrate",
                            "--host", "127.0.0.1"])


# ---------- 证据日志 ----------

def test_evidence_logger_jsonl_roundtrip(tmp_path):
    logger = EvidenceLogger(tmp_path / "logs" / "ev.jsonl")
    logger.log("device.attach", params={"busid": "6-4"}, result={"ok": True})
    logger.log("bridge.build", params={"project": "BESS_E521_DEV"},
               result={"mode": "dry-run"}, extra_marker="x")
    records = logger.read_all()
    assert len(records) == 2
    assert records[0]["command"] == "device.attach"
    assert records[0]["params"] == {"busid": "6-4"}
    assert records[1]["extra_marker"] == "x"
    assert all("ts" in r for r in records)


def test_evidence_logger_survives_bad_path(tmp_path, capsys):
    """证据目录不可写时只告警不抛（尽力而为语义）。"""
    logger = EvidenceLogger(tmp_path / "no-such-dir" / "sub" / "ev.jsonl")
    (tmp_path / "no-such-dir").write_text("占位文件，目录建不出来", encoding="utf-8")
    rec = logger.log("x", result={})
    assert rec["command"] == "x"
    assert "证据日志写入失败" in capsys.readouterr().err


# ---------- 决策数据结构 ----------

def test_authorization_decision_reason_text():
    d = AuthorizationDecision(granted=False, reasons=["a", "b"])
    assert d.reason_text == "a；b"
