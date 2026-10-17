"""AI-facing command failures remain a single machine-readable result."""
import json

from plc_idf import cli
from .conftest import write_cfg


def test_ctrl_c_is_structured_and_nonzero(tmp_path, monkeypatch, capsys, clean_env):
    config = write_cfg(tmp_path / "tool.toml", tmp_path / "evidence.jsonl")

    class InterruptedBackend:
        def install(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_open_backend", lambda args: InterruptedBackend())
    assert cli.main(["doctor", "--target", "open-linux", "--config", str(config), "--json"]) == 130
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["ok"] is False and report["diagnostics"][0]["code"] == "E_INTERRUPTED"
    assert "Traceback" not in captured.err
