"""devices / device 层：usbipd 输出解析、超时重试、attach/detach 门禁与前后校验。"""

import json
import subprocess
from pathlib import Path

import pytest

from plc_idf import cli
from plc_idf import device as device_mod
from plc_idf.config import load_config
from plc_idf.device import (DeviceEntry, attach_device, detach_device,
                           find_plc_entry, find_tty_devices, parse_usbipd_list,
                           probe_devices, render_devices_table)
from plc_idf.interop import InteropError, RetryPolicy, run_command
from plc_idf.safety import AuthorizationDecision, EvidenceLogger

from .conftest import USBIPD_SAMPLE, write_cfg

pytestmark = pytest.mark.usefixtures("clean_env")

GRANTED = AuthorizationDecision(granted=True, reasons=[])
DENIED = AuthorizationDecision(granted=False, reasons=["测试桩"])


# ---------- 解析 ----------

def test_parse_sample_entries():
    entries = parse_usbipd_list(USBIPD_SAMPLE)
    assert [e.busid for e in entries] == ["1-5", "2-1", "6-1", "6-2", "6-3", "6-4"]
    plc = entries[-1]
    assert plc.vidpid == "0471:a531"
    assert plc.state == "Attached" and plc.is_attached
    assert plc.description == "H5U USB Device"
    camera = entries[1]
    assert camera.description.startswith("Integrated Camera")
    assert not camera.is_attached


def test_parse_keeps_chinese_description():
    entries = parse_usbipd_list(USBIPD_SAMPLE)
    assert any("USB 输入设备" in e.description for e in entries)


def test_parse_error_state_row():
    text = "Connected:\nBUSID  VID:PID    DEVICE   STATE\n" \
           "1-3    1f3a:efe8  Unknown Device    Error\n"
    entries = parse_usbipd_list(text)
    assert len(entries) == 1
    assert entries[0].state == "Error" and not entries[0].is_attached


def test_parse_ignores_headers_and_persisted():
    entries = parse_usbipd_list(USBIPD_SAMPLE)
    assert all(e.vidpid.count(":") == 1 for e in entries)      # GUID 行没混进来
    assert not any("GUID" in e.description for e in entries)


def test_find_plc_entry_by_busid():
    # 显式配置而非 load_config()：测试不得依赖开发者本机的 .plcidf.toml
    class _Cfg:
        device = {"busid": "6-4", "vidpid": "0471:a531"}

    entries = parse_usbipd_list(USBIPD_SAMPLE)
    plc = find_plc_entry(_Cfg(), entries)
    assert plc is not None and plc.busid == "6-4"


def test_find_tty_devices(monkeypatch):
    monkeypatch.setattr(device_mod.glob, "glob",
                        lambda pat: ["/dev/ttyACM0"] if "ACM" in pat else [])
    assert find_tty_devices("/dev/ttyACM*") == ["/dev/ttyACM0"]
    monkeypatch.setattr(device_mod.glob, "glob", lambda pat: [])
    # ACM 空时回退探测 ttyUSB
    monkeypatch.setattr(device_mod.glob, "glob",
                        lambda pat: ["/dev/ttyUSB0"] if "USB" in pat else [])
    assert find_tty_devices("/dev/ttyACM*") == ["/dev/ttyUSB0"]


# ---------- probe / 渲染 ----------

class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_probe_devices_with_mock(tmp_path, monkeypatch):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                         extra=f'[device]\ntty_glob = "{(tmp_path / "acm*").as_posix()}"\n')
    cfg = load_config(str(cfg_path))
    (tmp_path / "acm0").write_text("")            # 假串口在场
    status = probe_devices(cfg, runner=lambda argv, timeout=None, cwd=None:
                           _Proc(0, stdout=USBIPD_SAMPLE))
    assert status.usbipd_ok and status.plc.is_attached
    assert status.tty == [str(tmp_path / "acm0")]


def test_probe_devices_degrades_when_usbipd_missing(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(device_mod, "find_tty_devices", lambda pat: ["/dev/ttyACM0"])

    def _missing(argv, timeout=None, cwd=None):
        raise FileNotFoundError(argv[0])
    status = probe_devices(cfg, runner=_missing)
    assert not status.usbipd_ok
    assert status.plc is None
    assert status.tty == ["/dev/ttyACM0"]


def test_render_devices_table(tmp_path, capsys):
    cfg = load_config()
    status = probe_devices(cfg, runner=lambda argv, timeout=None, cwd=None:
                           _Proc(0, stdout=USBIPD_SAMPLE))
    table = render_devices_table(status, busid_expected=cfg.device["busid"])
    assert "6-4" in table and "H5U" in table and "Attached" in table
    assert "PLC" in table


# ---------- 超时与重试上限 ----------

def test_run_command_retries_then_succeeds():
    attempts = {"n": 0}

    def flaky(argv, timeout=None, cwd=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise subprocess.TimeoutExpired(argv, 0.1)
        return _Proc(0, stdout="fine")
    result = run_command(["x"], policy=RetryPolicy(1.0, retries=2, backoff_s=0),
                         runner=flaky)
    assert result.ok and result.attempts == 3 and attempts["n"] == 3


def test_run_command_gives_up_after_retry_limit():
    def always_timeout(argv, timeout=None, cwd=None):
        raise subprocess.TimeoutExpired(argv, 0.1)
    result = run_command(["x"], policy=RetryPolicy(0.1, retries=1, backoff_s=0),
                         runner=always_timeout)
    assert not result.ok and result.timed_out and result.returncode == 124
    assert result.attempts == 2                   # 1 + retries


def test_list_windows_usb_raises_on_bad_rc():
    cfg = load_config()
    with pytest.raises(InteropError):
        device_mod.list_windows_usb(
            cfg, runner=lambda argv, timeout=None, cwd=None:
            _Proc(7, stdout="", stderr="boom"),
            policy=RetryPolicy(1.0, retries=0, backoff_s=0))


def test_list_windows_usb_missing_binary():
    cfg = load_config()
    with pytest.raises(InteropError) as exc:
        device_mod.list_windows_usb(
            cfg, runner=lambda argv, timeout=None, cwd=None:
            (_ for _ in ()).throw(FileNotFoundError("usbipd.exe")),
            policy=RetryPolicy(1.0, retries=0, backoff_s=0))
    assert "不存在" in str(exc.value)


# ---------- CLI devices 降级 ----------

def test_cli_devices_degrades_without_usbipd(tmp_cfg, capsys, monkeypatch):
    monkeypatch.setattr(device_mod, "run_command",
                        lambda *a, **k: (_ for _ in ()).throw(
                            InteropError("interop 可执行文件不存在")))
    rc = cli.main(["devices", "--config", str(tmp_cfg), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "不可用" in out                       # usbipd 降级提示
    payload = json.loads(out[out.index("{"):])   # --json 段仍可解析
    assert payload["usbipd_ok"] is False


# ---------- attach / detach ----------

class FakeUsbipd:
    """带状态的 usbipd 桩：list 反映当前状态，bind/attach/detach 改状态。"""

    def __init__(self, initial: str = "Shared", obey: bool = True):
        self.state = initial
        self.obey = obey
        self.ops: list[str] = []

    def __call__(self, argv, timeout=None, cwd=None):
        if argv[-1] == "list":
            return _Proc(0, stdout=USBIPD_SAMPLE.replace("Attached", self.state))
        op = argv[1]
        self.ops.append(op)
        if self.obey:
            if op == "bind":
                self.state = "Shared"
            elif op == "attach":
                self.state = "Attached"
            elif op == "detach":
                self.state = "Not shared"
        return _Proc(0, stdout=f"{op} ok")


@pytest.fixture
def tty_present(monkeypatch):
    monkeypatch.setattr(device_mod, "find_tty_devices",
                        lambda pat: ["/dev/ttyACM0"])


@pytest.fixture
def tty_absent(monkeypatch):
    monkeypatch.setattr(device_mod, "find_tty_devices", lambda pat: [])


def _cfg(tmp_path, settle=0.2):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                         extra=f"[device]\nattach_settle_s = {settle}\n")
    return load_config(str(cfg_path))


def test_attach_refused_without_authorization(tmp_path):
    with pytest.raises(Exception) as exc:
        attach_device(_cfg(tmp_path), DENIED, evidence=None, runner=FakeUsbipd())
    assert "门禁" in str(exc.value)


def test_attach_happy_path_from_shared(tmp_path, tty_present):
    cfg = _cfg(tmp_path)
    fake = FakeUsbipd("Shared")
    ev = EvidenceLogger(tmp_path / "ev.jsonl")
    attach_device(cfg, GRANTED, evidence=ev, runner=fake)
    assert fake.state == "Attached"
    assert fake.ops == ["attach"]                 # 已 bind，不再重复 bind
    assert any(r["command"] == "device.attach"
               for r in EvidenceLogger(tmp_path / "ev.jsonl").read_all())


def test_attach_binds_when_not_shared(tmp_path, tty_present):
    fake = FakeUsbipd("Not shared")
    attach_device(_cfg(tmp_path), GRANTED, evidence=None, runner=fake)
    assert fake.ops == ["bind", "attach"]
    assert fake.state == "Attached"


def test_attach_aborts_when_device_missing(tmp_path, tty_present):
    cfg_path = write_cfg(tmp_path / "c.toml", tmp_path / "ev.jsonl",
                         extra='[device]\nbusid = "9-9"\nvidpid = "0000:0000"\n')
    with pytest.raises(InteropError) as exc:
        attach_device(load_config(str(cfg_path)), GRANTED, evidence=None,
                      runner=FakeUsbipd())
    assert "前置校验" in str(exc.value)


def test_attach_postcheck_failure(tmp_path, tty_absent):
    """attach 命令"成功"但设备没真正挂载/串口没出现：后置校验必须失败。"""
    fake = FakeUsbipd("Shared", obey=False)       # 状态不变 -> Attached 永远不出现
    with pytest.raises(InteropError) as exc:
        attach_device(_cfg(tmp_path, settle=0.3), GRANTED, evidence=None,
                      runner=fake)
    assert "后置校验" in str(exc.value)


def test_detach_happy_path(tmp_path, tty_absent):
    fake = FakeUsbipd("Attached")
    ev = EvidenceLogger(tmp_path / "ev.jsonl")
    detach_device(_cfg(tmp_path), GRANTED, evidence=ev, runner=fake)
    assert fake.ops == ["detach"]
    assert fake.state == "Not shared"


def test_detach_refused_without_authorization(tmp_path, tty_absent):
    with pytest.raises(Exception) as exc:
        detach_device(_cfg(tmp_path), DENIED, evidence=None,
                      runner=FakeUsbipd("Attached"))
    assert "门禁" in str(exc.value)


def test_detach_precheck_when_not_attached(tmp_path, tty_absent):
    with pytest.raises(InteropError) as exc:
        detach_device(_cfg(tmp_path), GRANTED, evidence=None,
                      runner=FakeUsbipd("Shared"))
    assert "前置校验" in str(exc.value)
