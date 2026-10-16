"""sim 闭环：真实起虚拟从站进程再收（READY 探测 / 一轮监测 / 干净收尾）。"""

import json
import subprocess
import sys

import pytest

from plc_idf import cli
from plc_idf.backends.monitor_modbus import MonitorModbusBackend
from plc_idf.backends.sim_virtual import SimVirtualBackend, find_free_port
from plc_idf.config import load_config
from plc_idf.safety import EvidenceLogger, ReadOnlyViolation

from .conftest import REPO_ROOT, write_cfg

pytestmark = pytest.mark.usefixtures("clean_env")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    return load_config(str(write_cfg(tmp_path / "c.toml",
                                     tmp_path / "evidence.jsonl")))


# ---------- argv 组装与只读闸门 ----------

def test_sim_build_command(cfg):
    backend = SimVirtualBackend(cfg)
    argv = backend.build_command({"scenario": "overload", "port": 15430,
                                  "quiet": True})
    joined = " ".join(argv)
    assert "virtual_easy521.py" in joined
    assert "-u" in argv                            # 非缓冲输出（READY 探测依赖）
    assert "--scenario overload" in joined
    assert "--port 15430" in joined
    assert "--quiet" in joined


def test_monitor_build_command_gates_writes(cfg):
    backend = MonitorModbusBackend(cfg)
    with pytest.raises(ReadOnlyViolation):
        backend.build_command({"extra": ["--fc-write", "5"]})


def test_find_free_port_binds():
    port = find_free_port()
    assert 1024 < port < 65536


# ---------- 真实闭环：起从站 -> READY -> 一轮只读监测 -> 收尾 ----------

def test_run_with_monitor_loop_real(cfg, tmp_path):
    ev = EvidenceLogger(tmp_path / "ev.jsonl")
    backend = SimVirtualBackend(cfg)
    out = backend.run_with_monitor(
        {"scenario": "low_voltage", "quiet": True, "ready_timeout": 15.0},
        {"once": True}, evidence=ev)
    assert out["monitor_rc"] == 0
    assert "Easy521 监测" in out["monitor_output"]
    assert "Vpu" in out["monitor_output"]          # 契约量在表格里
    assert "sim_argv" in out and "-u" in out["sim_argv"]
    commands = [r["command"] for r in ev.read_all()]
    assert "sim.start" in commands and "monitor.run" in commands \
        and "sim.loop.done" in commands


def test_cli_sim_with_monitor_once(tmp_path, monkeypatch, capsys):
    """CLI 一键闭环冒烟：plcidf sim --with-monitor --once。"""
    monkeypatch.chdir(REPO_ROOT)
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "evidence.jsonl")
    rc = cli.main(["sim", "--with-monitor", "--once", "--scenario", "steady",
                   "--quiet", "--config", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Easy521 监测" in out
    assert "Vpu" in out
    events = [json.loads(ln)
              for ln in (tmp_path / "evidence.jsonl").read_text(
                  encoding="utf-8").splitlines()]
    assert any(e["command"] == "sim.loop.done" for e in events)


def test_cli_sim_once_requires_with_monitor(tmp_path, capsys):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "evidence.jsonl")
    rc = cli.main(["sim", "--once", "--config", str(cfg_path)])
    assert rc == 2
    assert "--with-monitor" in capsys.readouterr().err


# ---------- wait_ready 异常路径 ----------

def test_wait_ready_timeout_kills_child(cfg):
    backend = SimVirtualBackend(cfg)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    with pytest.raises(TimeoutError) as exc:
        backend.wait_ready(proc, 1.5)
    assert proc.poll() is not None                 # 超时路径必须收掉子进程


def test_wait_ready_child_exit_before_ready(cfg):
    backend = SimVirtualBackend(cfg)
    proc = subprocess.Popen(
        [sys.executable, "-c", "print('starting'); raise SystemExit(3)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with pytest.raises(TimeoutError) as exc:
        backend.wait_ready(proc, 10.0)
    assert "提前退出" in str(exc.value) and "rc=3" in str(exc.value)
