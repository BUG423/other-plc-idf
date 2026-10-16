"""plc_idf 测试共享配置：包导入路径 + 常用夹具。"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]      # src/plc_idf/tests -> 项目根
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

#: usbipd list 实测样本（2026-10-16，含中文描述与各类状态；末行 Error 为探针档案形态）
USBIPD_SAMPLE = """Connected:
BUSID  VID:PID    DEVICE                                                        STATE
1-5    0489:e0cd  MediaTek Bluetooth Adapter                                    Not shared
2-1    174f:181d  Integrated Camera, Integrated IR Camera, Camera DFU Device    Not shared
6-1    046d:c52b  Logitech USB Input Device, USB 输入设备                           Not shared
6-2    24ae:2015  USB 输入设备                                                      Not shared
6-3    1ea7:0066  USB 输入设备                                                      Not shared
6-4    0471:a531  H5U USB Device                                                Attached

Persisted:
GUID                                  DEVICE
"""

# 桥式 build/flash 场景的有效状态：设备在 Windows 侧（未挂载到 WSL，AutoShop 可见）
USBIPD_SAMPLE_ONWIN = USBIPD_SAMPLE.replace("Attached", "Shared")


def write_cfg(path: Path, evidence: Path, extra: str = "") -> Path:
    """写一个最小 .plcidf.toml（证据日志进 tmp，其余默认）。"""
    path.write_text(
        f'[log]\nevidence = "{evidence.as_posix()}"\n' + extra, encoding="utf-8")
    return path


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def clean_env(monkeypatch):
    """清掉可能污染配置发现的环境变量（新名 + 旧名兼容别名都清）。"""
    for name in ("PLCIDF_CONFIG", "PLCIDF_ROOT",
                 "PY2PLC_CONFIG", "PY2PLC_ROOT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def tmp_cfg(tmp_path, clean_env, monkeypatch):
    """默认测试配置：chdir 项目根（稳定自动发现）+ 证据日志进 tmp。"""
    monkeypatch.chdir(REPO_ROOT)
    return write_cfg(tmp_path / "plc_idf.toml", tmp_path / "evidence.jsonl")


@pytest.fixture
def tmp_cfg_unlocked(tmp_path, clean_env, monkeypatch):
    """bridge.enabled=true 的配置（配合 --i-have-authorized 测已解锁路径）。"""
    monkeypatch.chdir(REPO_ROOT)
    return write_cfg(tmp_path / "plc_idf.toml", tmp_path / "evidence.jsonl",
                     extra="[bridge]\nenabled = true\n")
