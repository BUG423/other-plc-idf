"""Distribution-related path resolution; no Windows or PLC access."""

from pathlib import Path

import plc_idf.config as config


def test_installed_package_uses_workspace_and_bundled_assets(tmp_path, monkeypatch,
                                                          clean_env):
    package = tmp_path / "venv/lib/python3.12/site-packages/plc_idf"
    package.mkdir(parents=True)
    fake_module = package / "config.py"
    fake_module.touch()
    monitor = package / "_resources/wsl-tools/e521_monitor.py"
    bridge = package / "_resources/plc/tools/autoshop_ui.ps1"
    for asset in (monitor, bridge):
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.touch()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(config, "__file__", str(fake_module))
    monkeypatch.chdir(work)

    cfg = config.load_config()

    assert config.find_project_root() == work
    assert Path(cfg.paths.wsl_tools) == monitor.parent
    assert config.evidence_path(cfg) == work / "logs/evidence.jsonl"
    assert str(bridge).replace("/", "\\") in cfg.bridge.tools_ps1_windows


def test_nearest_workspace_config_is_found_without_source_marker(tmp_path,
                                                                monkeypatch,
                                                                clean_env):
    workspace = tmp_path / "user-project"
    child = workspace / "src/control"
    child.mkdir(parents=True)
    (workspace / config.CONFIG_NAME).write_text(
        '[monitor]\nhost = "192.168.10.10"\n', encoding="utf-8")
    monkeypatch.chdir(child)

    cfg = config.load_config()

    assert Path(cfg.paths.project_root) == workspace
    assert cfg.source == workspace / config.CONFIG_NAME
    assert cfg.monitor.host == "192.168.10.10"
    assert (Path(cfg.paths.wsl_tools) / "e521_regs.py").is_file()


def test_explicit_tool_directory_is_resolved_from_workspace(tmp_path, clean_env):
    cfg_path = tmp_path / config.CONFIG_NAME
    cfg_path.write_text('[paths]\nwsl_tools = "custom-tools"\n', encoding="utf-8")

    cfg = config.load_config(cfg_path, project_root=tmp_path)

    assert Path(cfg.paths.wsl_tools) == tmp_path / "custom-tools"


def test_loading_config_never_mutates_defaults(tmp_path, clean_env):
    first = config.load_config(project_root=tmp_path / "first")
    second = config.load_config(project_root=tmp_path / "second")

    assert config.DEFAULTS["paths"] == {"project_root": None, "wsl_tools": None}
    assert config.DEFAULTS["bridge"]["tools_ps1_windows"] is None
    assert first.paths.project_root != second.paths.project_root
