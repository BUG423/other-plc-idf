"""桥式执行的失败语义回归；所有 Windows/GUI 调用均以假 runner 截获。"""

import subprocess
from types import SimpleNamespace

import pytest

from plc_idf import interop
from plc_idf.backends.base import OperationRequest
from plc_idf.backends.bridge_autoshop import BridgeAutoshopBackend
from plc_idf.config import load_config
from plc_idf.safety import EvidenceLogger, check_bridge_authorization

from .conftest import USBIPD_SAMPLE_ONWIN, write_cfg

pytestmark = pytest.mark.usefixtures("clean_env")


@pytest.mark.parametrize("literal", ['"false"', '"true"', "0", "1", "[]", "{}"])
def test_non_boolean_enabled_never_authorizes(tmp_path, literal):
    path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                     extra=f"[bridge]\nenabled = {literal}\n")
    decision = check_bridge_authorization(load_config(path), True)
    assert decision.granted is False
    assert decision.factors["config_bridge_enabled"] is False


def _config(tmp_path):
    return load_config(write_cfg(
        tmp_path / "c.toml", tmp_path / "ev.jsonl",
        extra="[bridge]\nenabled = true\n"
              "[net]\nretries = 2\nretry_backoff_s = 0.0\n"))


def _response(argv):
    if argv[-1] == "list":
        text = USBIPD_SAMPLE_ONWIN
    elif "inspect" in argv:
        text = "PID=123|AutoShop WRONG_PROJECT"
    elif "log" in argv:
        text = "编译失败，1 个错误；下载失败"
    else:
        text = "GUI-ok"
    return SimpleNamespace(returncode=0, stdout=text, stderr="")


@pytest.mark.parametrize("action", ["build", "flash"])
def test_zero_exit_codes_without_verification_cannot_report_success(
        tmp_path, monkeypatch, action):
    """错误标题/失败日志/无产物仍 rc=0：必须记录未核验并返回失败。"""
    monkeypatch.setattr(interop, "_default_runner", lambda argv, **kw: _response(argv))
    cfg = _config(tmp_path)
    evidence = EvidenceLogger(tmp_path / "ev.jsonl")
    backend = BridgeAutoshopBackend(cfg, evidence)
    request = OperationRequest("MISSING_PROJECT", r"Z:\does-not-exist",
                               dry_run=False, authorized=True)
    result = getattr(backend, f"execute_{action}")(request)
    assert result.ok is False
    assert result.outputs["verification_status"] == "unverified"
    assert result.outputs["command_chain_completed"] is True
    assert result.outputs["unverified_steps"]
    assert "结果未核验" in result.message
    events = evidence.read_all()
    missing = [e for e in events if e["command"] == f"bridge.{action}.verification"]
    assert missing and all(e["result"]["ok"] is False for e in missing)
    done = events[-1]
    assert done["command"] == f"bridge.{action}.done"
    assert done["result"]["ok"] is False
    assert done["result"]["command_chain_completed"] is True


@pytest.mark.parametrize("action,ui_action,value", [
    ("build", "keys", "{F7}"),
    ("flash", "keys", "{F8}"),
    ("flash", "keys", "{ENTER}"),
    ("build", "file", None),
    ("build", "accept", None),
])
def test_mutating_gui_timeout_is_never_replayed(
        tmp_path, monkeypatch, action, ui_action, value):
    attempts = []

    def runner(argv, **kwargs):
        if "-Action" in argv and argv[argv.index("-Action") + 1] == ui_action:
            if value is None or value in argv:
                attempts.append(list(argv))
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return _response(argv)

    monkeypatch.setattr(interop, "_default_runner", runner)
    backend = BridgeAutoshopBackend(_config(tmp_path))
    request = OperationRequest("BESS_E521_DEV", r"Z:\does-not-exist",
                               dry_run=False, authorized=True)
    result = getattr(backend, f"execute_{action}")(request)
    assert result.ok is False
    assert len(attempts) == 1  # 配置允许 2 次重试，但有副作用的动作只发一次
    assert result.outputs["failed_step"]


def test_readonly_gui_query_can_retry(tmp_path, monkeypatch):
    attempts = []

    def runner(argv, **kwargs):
        if "inspect" in argv:
            attempts.append(list(argv))
            if len(attempts) == 1:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return _response(argv)

    monkeypatch.setattr(interop, "_default_runner", runner)
    result = BridgeAutoshopBackend(_config(tmp_path)).execute_build(
        OperationRequest("BESS_E521_DEV", r"Z:\does-not-exist",
                         dry_run=False, authorized=True))
    assert len(attempts) == 2
    assert result.outputs["command_chain_completed"] is True
    assert result.outputs["verification_status"] == "unverified"


@pytest.mark.parametrize("action", ["build", "flash"])
def test_dry_run_explains_missing_required_verification(tmp_path, action):
    cfg = _config(tmp_path)
    request = OperationRequest("BESS_E521_DEV", r"Z:\does-not-exist")
    text = BridgeAutoshopBackend(cfg).render_dry_run(
        action, request, check_bridge_authorization(cfg, False))
    assert "必需校验尚未实现" in text
    assert "执行调用链后仍返回未核验" in text
