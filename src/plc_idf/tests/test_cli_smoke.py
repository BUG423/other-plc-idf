"""CLI 冒烟：各子命令 --help / build-flash dry-run 输出含关键调用链要素。"""

import os

import pytest

from plc_idf import cli
from plc_idf.config import load_config

from .conftest import write_cfg

pytestmark = pytest.mark.usefixtures("clean_env")


# ---------- 顶层与 --help ----------

def test_main_help_lists_all_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("build", "flash", "monitor", "sim", "devices"):
        assert name in out, f"--help 缺少子命令 {name}"


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "plcidf" in out and "0.2.0" in out


def test_prog_name_is_plcidf(capsys):
    """用法错误信息应以新命令名 plcidf 自称（prog 名）。"""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--no-such-flag"])
    assert exc.value.code == 2
    assert "plcidf" in capsys.readouterr().err


@pytest.mark.parametrize("sub", ["build", "flash", "monitor", "sim", "devices"])
def test_subcommand_help(sub, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([sub, "--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out  # 有帮助文本


# ---------- build / flash dry-run 关键要素 ----------

def test_build_dry_run_contains_chain_elements(tmp_cfg, capsys, monkeypatch):
    """dry-run 必须打印：powershell 调用、autoshop_ui.ps1、Windows 工程路径、
    .hcp 工程文件、usbipd 校验步与预期产物；且绝不执行任何 interop。"""
    def _boom(*_a, **_kw):        # dry-run 期间任何 interop 调用都是缺陷
        raise AssertionError("dry-run 不允许执行 interop 调用")
    monkeypatch.setattr("plc_idf.backends.bridge_autoshop.run_command", _boom)

    rc = cli.main(["build", "--config", str(tmp_cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    for token in ("bridge_autoshop", "dry-run", "powershell",
                  "autoshop_ui.ps1", "BESS_E521_DEV.hcp",
                  "E:\\PLCProjects\\BESS_E521_DEV", "usbipd", "预期产物"):
        assert token in out, f"build dry-run 输出缺少关键要素: {token}"


def test_flash_dry_run_contains_chain_elements(tmp_cfg, capsys):
    rc = cli.main(["flash", "--config", str(tmp_cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "下载到 PLC" in out
    assert "{F8}" in out                         # 下载触发步（4.12 实测路径）
    assert "BESS_E521_DEV.hcp" in out             # 打开工程步
    assert "解锁真执行" in out


def test_dry_run_is_default(tmp_cfg, capsys, monkeypatch):
    """不带任何旗标的 build 就是 dry-run（不执行、打印调用链）。"""
    monkeypatch.setattr("plc_idf.backends.bridge_autoshop.run_command",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("默认必须是 dry-run")))
    assert cli.main(["build", "--config", str(tmp_cfg)]) == 0
    assert "dry-run" in capsys.readouterr().out


# ---------- monitor 透传与只读闸门 ----------

def test_monitor_help_shows_forwarded_options(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["monitor", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--calibrate", "--transport", "--word-order", "--jsonl"):
        assert token in out


def test_monitor_rejects_write_token(tmp_cfg, capsys, monkeypatch):
    """透传层黑名单：写语义记号在执行前被拒（rc=2，说明只读边界）。"""
    def _boom(*_a, **_kw):
        raise AssertionError("闸门应在此之前拒绝，不应起子进程")
    monkeypatch.setattr("plc_idf.cli.subprocess.run", _boom)

    rc = cli.main(["monitor", "--config", str(tmp_cfg),
                   "--", "--fc-write", "5"])
    assert rc == 2
    assert "只读" in capsys.readouterr().err


def test_monitor_once_against_sim_output_shape(tmp_cfg, capsys):
    """monitor 单独冒烟放在 test_sim_loop 里（需要从站），这里只验证
    build_command 组装的 argv 透传了 --once/--calibrate。"""
    from plc_idf.backends.monitor_modbus import MonitorModbusBackend
    cfg = load_config(str(tmp_cfg))
    backend = MonitorModbusBackend(cfg)
    argv = backend.build_command({"once": True, "calibrate": True,
                                  "host": "127.0.0.1", "port": 15420})
    joined = " ".join(argv)
    assert "e521_monitor.py" in joined
    assert "--once" in joined and "--calibrate" in joined
    assert "--port" in joined and "15420" in joined


# ---------- devices 真实只读冒烟（有 usbipd 才跑） ----------

def test_devices_real_readonly(tmp_cfg, capsys):
    from pathlib import Path
    if not Path("/mnt/c/Program Files/usbipd-win/usbipd.exe").exists():
        pytest.skip("usbipd-win 未安装（非 WSL 环境）")
    rc = cli.main(["devices", "--config", str(tmp_cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "BUSID" in out and "WSL 串口" in out


# ---------- 更名与别名兼容（plc-idf / plcidf；曾用名 py2plc） ----------

def test_legacy_env_config_alias(tmp_path, monkeypatch):
    """旧环境变量 PY2PLC_CONFIG 仍被识别；新名 PLCIDF_CONFIG 优先。"""
    new_cfg = write_cfg(tmp_path / "new.toml", tmp_path / "ev_new.jsonl")
    old_cfg = write_cfg(tmp_path / "old.toml", tmp_path / "ev_old.jsonl")
    monkeypatch.chdir(tmp_path)                    # 无 .plcidf.toml，走环境变量
    monkeypatch.setenv("PY2PLC_CONFIG", str(old_cfg))
    monkeypatch.delenv("PLCIDF_CONFIG", raising=False)
    assert load_config().source == old_cfg          # 旧名兜底生效
    monkeypatch.setenv("PLCIDF_CONFIG", str(new_cfg))
    assert load_config().source == new_cfg          # 新名优先


def test_new_default_config_name_discovered(tmp_path, monkeypatch):
    """默认配置文件名是 .plcidf.toml；旧名 .py2plc.toml 不再被自动发现。"""
    from plc_idf.config import CONFIG_NAME, find_config_file
    assert CONFIG_NAME == ".plcidf.toml"
    (tmp_path / ".py2plc.toml").write_text("[log]\n", encoding="utf-8")
    assert find_config_file(tmp_path) is None       # 旧名不认
    (tmp_path / ".plcidf.toml").write_text("[log]\n", encoding="utf-8")
    assert find_config_file(tmp_path) == tmp_path / ".plcidf.toml"


def test_console_script_alias_py2plc(tmp_path):
    """别名契约：py2plc 与 plcidf 指向同一 CLI，--version/--help 输出一致。

    两个入口都来自 pip install -e .（见 pyproject [project.scripts]）；
    未安装时跳过（源码层测试不依赖安装状态）。
    """
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    # 入口脚本在 venv 的 bin/（sys.prefix/bin，勿 resolve——python 是软链）
    bin_dir = Path(sys.prefix) / "bin"

    def _find(name: str) -> str | None:
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
        return shutil.which(name)

    plcidf, py2plc = _find("plcidf"), _find("py2plc")
    if not (plcidf and py2plc):
        pytest.skip("console scripts 未安装（需先 pip install -e .）")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    run = lambda exe: subprocess.run(
        [exe, "--version"], capture_output=True, text=True, check=False,
        timeout=30, env=env)
    new, old = run(plcidf), run(py2plc)
    assert new.returncode == 0 and old.returncode == 0
    assert new.stdout.strip() == old.stdout.strip()   # 同一 CLI，同一版本串
    assert new.stdout.startswith("plcidf ")
    assert sys.version_info[:2] >= (3, 12)            # 与 pyproject 要求呼应
