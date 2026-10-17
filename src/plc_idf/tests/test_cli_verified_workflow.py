"""Machine-readable preview and refusal contracts; no Windows operations."""

import json

import pytest

from plc_idf import cli
from plc_idf import native_autoshop
from plc_idf.backends import bridge_autoshop


@pytest.mark.parametrize("action", ["build", "flash"])
def test_bridge_json_preview_is_one_document_without_commands(tmp_cfg, monkeypatch, capsys, action):
    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not execute Windows commands")
    monkeypatch.setattr(bridge_autoshop, "run_command", forbidden)
    assert cli.main([action, "--json", "--config", str(tmp_cfg)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1 and result["executed"] is False
    assert result["status"] == "preview"


def test_compile_requires_explicit_working_copy_before_ui(tmp_cfg_unlocked, monkeypatch, capsys):
    monkeypatch.setattr(native_autoshop, "run_command", lambda *a, **k: pytest.fail("UI accessed"))
    rc = cli.main(["build", "--source", "source.st", "--execute", "--i-have-authorized",
                   "--json", "--config", str(tmp_cfg_unlocked)])
    assert rc == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and "working-project" in result["diagnostics"][0]["message"]


def test_compile_refuses_missing_authorization_before_ui(tmp_cfg_unlocked, monkeypatch, capsys):
    monkeypatch.setattr(native_autoshop, "run_command", lambda *a, **k: pytest.fail("UI accessed"))
    rc = cli.main(["build", "--source", "source.st", "--working-project", r"E:\PLCProjects\BESS_E521_DEV",
                   "--execute", "--json", "--config", str(tmp_cfg_unlocked)])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_monitor_calibration_config_is_not_overridden_by_absent_flag(tmp_cfg, monkeypatch, capsys):
    parser = cli.build_parser()
    assert parser.parse_args(["monitor"]).calibrate is None
    assert parser.parse_args(["sim"]).calibrate is None
    assert parser.parse_args(["monitor", "--calibrate"]).calibrate is True
