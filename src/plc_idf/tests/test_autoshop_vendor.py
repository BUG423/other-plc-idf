"""Offline response fixtures exercise guards, not real device acceptance."""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from plc_idf.autoshop_vendor import (
    AutoShopVendorAdapter, COMPARE_DIFFERENT, COMPARE_EQUAL, VendorAdapterError,
    analyze_download_log, fresh_log_lines,
)
from plc_idf.evidence import EvidenceWriteError
from .test_native_release import release_fixture  # pytest fixture, synthetic native project


PROJECT = r"E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV"
TITLE = "AutoShop V4.12.0.2  " + PROJECT + " - [MAIN]"
BASELINE = "information from a previous operation"
DOWNLOAD = BASELINE + "\n" + "\n".join([
    "信息 (2026-10-17 22:44:13) 开始下载",
    "信息 下载project 成功", "信息 下载userprog.foid 成功",
    "信息 下载stprog.o 成功", "信息 下载stuserprog.o 成功", "信息 下载成功！",
])
COMMUNICATION_DOWNLOAD = "----------------------------------  AutoShop : 通讯消息  ----------------------------------\n" + "\n".join([
    f"信息  (2026-10-17 23:08:46)  {message}" for message in
    ("开始下载", "下载project 成功", "下载userprog.foid 成功", "下载stprog.o 成功",
     "下载stuserprog.o 成功", "下载成功！")])
WINDOW = {"started_at": "2026-10-17T23:08:45.500000+08:00",
          "observed_at": "2026-10-17T23:08:47.500000+08:00"}


class FixtureRunner:
    """Synthetic response dispatcher; never delegates to subprocess.run."""

    def __init__(self):
        self.calls = []
        self.title, self.read_only = TITLE, False
        self.usb = "6-4    0471:a531    H5U USB Device    Shared\n"
        self.rect = "-16,-16,3872,2096"
        self.diagnostics = ("AppPID=21248 Session=3 Foreground=396572 ForegroundPID=21248 "
                            "AppDPI=96 SystemDPI=192 Screen=3840x2160")
        self.controls = [
            {"name": name, "class": "Button", "checked": checked,
             "button_type": kind, "enabled": True}
            for name, checked, kind in [
                ("下载工程源码(不勾选则不能上载工程)", 1, 3),
                ("下载配置在工程中的PLC登录密码", 0, 3),
                ("下载时，保持型变量保持现有值", 1, 9),
                ("下载时，保持型变量重新初始化", 0, 9),
                ("确定", 0, 1),
            ]]
        self.downloaded = self.compared = False
        self.compare_message = COMPARE_EQUAL
        self.log_override = None
        self.hook = None
        self.fail_action = None
        self.unexpected_modal = False
        self.clock_reads = 0
        self.clock_values = None

    def __call__(self, argv, timeout, cwd=None):
        assert 0 < timeout <= 30
        argv = list(argv)
        self.calls.append(argv)
        if "-Action" not in argv:
            if "-Command" in argv:
                assert argv[-1] == "[DateTimeOffset]::Now.ToString('o')"
                output = ((WINDOW["started_at"] if self.clock_reads == 0 else WINDOW["observed_at"])
                          if self.clock_values is None else self.clock_values[min(self.clock_reads, len(self.clock_values) - 1)])
                self.clock_reads += 1
                return SimpleNamespace(returncode=0, stdout=output, stderr="")
            assert argv[-1] == "list"
            return SimpleNamespace(returncode=0, stdout=self.usb, stderr="")
        action = argv[argv.index("-Action") + 1]
        value = argv[argv.index("-Value") + 1] if "-Value" in argv else ""
        if action == self.fail_action:
            return SimpleNamespace(returncode=1, stdout="", stderr="no input sent")
        if self.hook:
            self.hook(action, value)
        if action == "editor-read":
            output = {"title": self.title, "source": "probe := probe + 1;", "read_only": self.read_only}
        elif action == "inspect":
            output = (f"PID=21248|{self.title}\nControlType.Window|{self.title}|"
                      f"Afx:400000|396572|True|{self.rect}")
            if self.compared or self.unexpected_modal:
                output += "\nControlType.Window|AutoShop|#32770|1119332|True|1648,864,544,338"
        elif action == "diagnostics":
            output = self.diagnostics
        elif action == "status":
            output = {"title": self.title, "process_id": 21248,
                      "controls": [{"name": "就绪", "class": "StatusBar"}]}
        elif action == "dialog":
            dialog = argv[argv.index("-ExpectedDialog") + 1]
            output = {"title": dialog, "handle": 1119332, "project": self.title,
                      "controls": (self.controls if dialog == "下载设置" else
                                   [{"name": self.compare_message, "class": "Static"}])}
        elif action == "button":
            dialog = argv[argv.index("-ExpectedDialog") + 1]
            if dialog == "下载设置":
                self.downloaded = True
            output = {"dialog": dialog, "button": value, "invocation_posted": True}
        elif action == "keys":
            assert value == "{F8}"
            output = ""
        elif action == "click":
            coordinates = (argv[argv.index("-X") + 1], argv[argv.index("-Y") + 1])
            assert coordinates in {("520", "86"), ("600", "864")}
            if coordinates == ("600", "864"):
                self.compared = True
            output = ""
        elif action == "log-data":
            output = self.log_override
            if output is None:
                output = DOWNLOAD if self.downloaded else BASELINE
                if self.compared:
                    output += "\n信息 " + self.compare_message
        else:
            raise AssertionError(f"Unmocked action: {action}")
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    @property
    def actions(self):
        return [argv[argv.index("-Action") + 1] for argv in self.calls if "-Action" in argv]


@pytest.fixture
def setup(tmp_path):
    cfg = SimpleNamespace(bridge={"windows_project": PROJECT, "powershell_exe": "NO-WINDOWS",
                                   "tools_ps1_windows": "mock-ui.ps1"},
                          device={"busid": "6-4", "vidpid": "0471:a531", "usbipd_exe": "NO-USB"})
    artifact = tmp_path / "MAIN.o"
    artifact.write_bytes(b"synthetic artifact, not vendor code")
    context = {"operation_id": "unit-operation", "build_hash": "unit-build", "working_project": PROJECT,
               "target": {"busid": "6-4", "vidpid": "0471:a531"}, "auto_run": False,
               "artifacts": [{"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    runner = FixtureRunner()
    adapter = AutoShopVendorAdapter(cfg, evidence_directory=tmp_path / "evidence", runner=runner)
    return adapter, runner, context


def assert_evidence(result):
    refs = result["evidence"]
    assert refs
    for ref in refs:
        data = Path(ref["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == ref["sha256"]
        assert json.loads(data)["schema"] == "plcidf.autoshop-vendor-evidence.v1"
    assert len({ref["path"] for ref in refs}) == len(refs)


def test_constructor_and_metadata_are_inert(setup):
    adapter, runner, _ = setup
    assert adapter.describe()["capabilities"]["program_compare"] is True
    assert adapter.describe()["capabilities"]["runtime_control"] is False
    assert not runner.calls
    assert not adapter.evidence_directory.exists()


def test_download_and_independent_compare_retained_separately(setup):
    adapter, runner, context = setup
    preflight = adapter.preflight(context, timeout_s=5)
    assert preflight["status"] == "verified"
    downloaded = adapter.download(context, timeout_s=5)
    assert downloaded["status"] == "completed"
    verified = adapter.verify_download(context, downloaded, timeout_s=5)
    assert verified["status"] == "verified"
    compared = adapter.compare_program(context, timeout_s=5)
    assert compared["status"] == "verified" and compared["matches"] is True
    assert compared["independent"] is True and compared["artifact_integrity"] is True
    for result in (preflight, downloaded, verified, compared):
        assert result["operation_id"] == context["operation_id"]
        assert result["run_requested"] is False
        assert_evidence(result)
    assert runner.actions.count("keys") == 1
    assert runner.actions.count("click") == 2
    all_refs = sum((r["evidence"] for r in (preflight, downloaded, verified, compared)), [])
    assert len({r["path"] for r in all_refs}) == len(all_refs)
    assert not ({r["sha256"] for r in verified["evidence"]} & {r["sha256"] for r in compared["evidence"]})


@pytest.mark.parametrize("change", ["readonly", "dirty", "project", "version", "modal", "rect", "dpi", "foreground", "usb", "ambiguous"])
def test_preflight_guards_refuse_before_input(setup, change):
    adapter, runner, context = setup
    if change == "readonly":
        runner.read_only = True
    elif change == "dirty":
        runner.title = TITLE.replace("[MAIN]", "[MAIN*]")
    elif change == "project":
        runner.title = TITLE.replace("BESS_E521_DEV", "BESS_E521_ORIGINAL")
    elif change == "version":
        runner.title = TITLE.replace("4.12.0.2", "4.13.0.0")
    elif change == "modal":
        runner.unexpected_modal = True
    elif change == "rect":
        runner.rect = "0,0,1920,1080"
    elif change == "dpi":
        runner.diagnostics = runner.diagnostics.replace("AppDPI=96", "AppDPI=192")
    elif change == "foreground":
        runner.diagnostics = runner.diagnostics.replace("ForegroundPID=21248", "ForegroundPID=999")
    elif change == "usb":
        runner.usb = runner.usb.replace("6-4", "6-7")
    else:
        runner.usb += runner.usb
    result = adapter.preflight(context, timeout_s=5)
    assert result["status"] == "unavailable"
    assert_evidence(result)
    assert not set(runner.actions) & {"keys", "click", "button", "check"}


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_unsafe_download_settings_are_not_changed_or_confirmed(setup, index):
    adapter, runner, context = setup
    assert adapter.preflight(context, timeout_s=5)["status"] == "verified"
    runner.controls[index]["checked"] = 1 - runner.controls[index]["checked"]
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown"
    assert "button" not in runner.actions and "check" not in runner.actions
    assert runner.actions.count("keys") == 1
    assert_evidence(result)


def test_download_is_not_repeated_even_after_uncertain_key_failure(setup):
    adapter, runner, context = setup
    adapter.preflight(context, timeout_s=5)
    runner.fail_action = "keys"
    first = adapter.download(context, timeout_s=5)
    second = adapter.download(context, timeout_s=5)
    assert first["status"] == second["status"] == "unknown"
    assert runner.actions.count("keys") == 1
    assert "no input sent" in first["reason"]


def test_download_requires_exact_preflight_context(setup):
    adapter, runner, context = setup
    adapter.preflight(context, timeout_s=5)
    context["build_hash"] = "different build"
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown" and "exact operation" in result["reason"]
    assert "keys" not in runner.actions


def test_stale_success_is_not_a_new_download():
    assert analyze_download_log(DOWNLOAD, DOWNLOAD)["status"] == "unknown"
    assert analyze_download_log(BASELINE, DOWNLOAD)["status"] == "verified"
    assert analyze_download_log(BASELINE, DOWNLOAD.replace("下载stprog.o 成功", ""))["status"] == "unknown"
    assert analyze_download_log(BASELINE, DOWNLOAD + "\n下载失败")["status"] == "failed"
    assert analyze_download_log(BASELINE, DOWNLOAD + "\n开始下载")["status"] == "unknown"
    with pytest.raises(VendorAdapterError, match="truncated"):
        fresh_log_lines(DOWNLOAD, "开始下载\n下载成功！")


def test_replaced_real_format_log_requires_exact_action_clock_window():
    with pytest.raises(VendorAdapterError, match="truncated"):
        analyze_download_log(BASELINE, COMMUNICATION_DOWNLOAD)
    result = analyze_download_log(BASELINE, COMMUNICATION_DOWNLOAD, window=WINDOW)
    assert result["status"] == "verified"
    assert len(result["files"]) == 4
    assert analyze_download_log(BASELINE, COMMUNICATION_DOWNLOAD.replace("23:08:46", "23:08:45"),
                                window=WINDOW)["status"] == "unknown"
    wrong_window = {"started_at": "2026-10-17T23:09:45+08:00",
                    "observed_at": "2026-10-17T23:09:47+08:00"}
    assert analyze_download_log(BASELINE, COMMUNICATION_DOWNLOAD, window=wrong_window)["status"] == "unknown"


@pytest.mark.parametrize("replacement", [
    COMMUNICATION_DOWNLOAD.replace("通讯消息", "编译消息"),
    COMMUNICATION_DOWNLOAD.replace("23:08:46", "23:08:48"),
    COMMUNICATION_DOWNLOAD.replace("信息  (2026-10-17 23:08:46)", "信息"),
])
def test_clock_fallback_refuses_unknown_pane_future_and_undated_lines(replacement):
    with pytest.raises(VendorAdapterError):
        analyze_download_log(BASELINE, replacement, window=WINDOW)


def test_clocked_partial_start_waits_for_complete_trace():
    partial = COMMUNICATION_DOWNLOAD.splitlines()[:2]
    assert analyze_download_log(BASELINE, "\n".join(partial), window=WINDOW)["status"] == "unknown"


def test_clock_offset_change_cannot_validate_replaced_log():
    invalid = dict(WINDOW, observed_at="2026-10-17T15:08:47.500000+00:00")
    with pytest.raises(VendorAdapterError, match="clock window"):
        analyze_download_log(BASELINE, COMMUNICATION_DOWNLOAD, window=invalid)


def test_download_pane_switch_uses_fresh_complete_time_bound_trace(setup):
    adapter, runner, context = setup
    adapter.preflight(context, timeout_s=5)
    def switch_pane(action, value):
        if action == "button":
            runner.log_override = COMMUNICATION_DOWNLOAD
    runner.hook = switch_pane
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "completed"
    assert result["vendor_result"]["clock_window"] == WINDOW
    assert runner.actions.count("button") == 1
    assert runner.clock_reads == 2
    assert_evidence(result)


def test_replaced_log_does_not_trigger_another_download(setup):
    adapter, runner, context = setup
    adapter.preflight(context, timeout_s=5)
    def replace_after_confirm(action, value):
        if action == "button":
            runner.log_override = DOWNLOAD.replace(BASELINE, "different output pane")
    runner.hook = replace_after_confirm
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown" and "calibrated communication pane" in result["reason"]
    assert runner.actions.count("button") == 1


def test_vendor_auto_compile_invalidates_original_build(setup):
    adapter, runner, context = setup
    adapter.preflight(context, timeout_s=5)
    def mutate(action, value):
        if action == "button":
            Path(context["artifacts"][0]["path"]).write_bytes(b"vendor rebuilt")
    runner.hook = mutate
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown" and result["artifact_integrity"] is False
    assert result["vendor_result"]["status"] == "verified"
    assert "auto-compile" in result["reason"]
    assert_evidence(result)


@pytest.mark.parametrize("message,matches", [(COMPARE_EQUAL, True), (COMPARE_DIFFERENT, False)])
def test_compare_distinguishes_match_and_mismatch(setup, message, matches):
    adapter, runner, context = setup
    runner.compare_message = message
    result = adapter.compare_program(context, timeout_s=5)
    assert result["status"] == "verified" and result["matches"] is matches
    assert result["vendor_message"] == message
    assert result["method"] == "vendor-program-compare"
    assert runner.actions.count("click") == 2
    assert runner.actions.count("button") == 1
    assert "-ExpectedText" in runner.calls[-1]


def test_compare_does_not_click_on_uncalibrated_layout(setup):
    adapter, runner, context = setup
    runner.diagnostics = runner.diagnostics.replace("3840x2160", "1920x1080")
    result = adapter.compare_program(context, timeout_s=5)
    assert result["status"] == "unknown"
    assert "click" not in runner.actions


def test_compare_recompile_is_preserved_as_unknown_with_vendor_match(setup):
    adapter, runner, context = setup
    def mutate(action, value):
        if action == "button":
            Path(context["artifacts"][0]["path"]).write_bytes(b"recompiled during compare")
    runner.hook = mutate
    result = adapter.compare_program(context, timeout_s=5)
    assert result["status"] == "unknown" and result["matches"] is True
    assert result["artifact_integrity"] is False


def test_compare_requires_fresh_separate_log(setup):
    adapter, runner, context = setup
    runner.log_override = BASELINE + "\n信息 " + COMPARE_EQUAL
    result = adapter.compare_program(context, timeout_s=5)
    assert result["status"] == "unknown"
    assert "fresh log" in result["reason"]
    assert "button" not in runner.actions


def test_connection_usb_and_values_cannot_assert_online(setup):
    adapter, runner, _ = setup
    runner.title = TITLE.replace("[MAIN]", "[变量表]")
    result = adapter.observe_connection(timeout_s=5)
    assert result["ok"] is result["online"] is result["connection_verified"] is False
    assert result["status"] == "unverified"
    assert result["usb"]["state"] == "Shared" and result["ui_project_verified"] is True
    assert result["vendor_info"] == {}
    assert runner.actions == ["status"]
    assert_evidence(result)


def test_connection_wrong_project_remains_unverified(setup):
    adapter, runner, _ = setup
    runner.title = TITLE.replace("BESS_E521_DEV", "OTHER")
    result = adapter.observe_connection(timeout_s=5)
    assert result["status"] == "unverified" and result["online"] is False
    assert "exact calibrated working project" in result["reason"]


def test_failed_evidence_write_prevents_input(setup):
    adapter, runner, context = setup
    adapter.evidence_directory.write_text("cannot be a directory")
    with pytest.raises(EvidenceWriteError):
        adapter.preflight(context, timeout_s=5)
    assert "keys" not in runner.actions and "click" not in runner.actions


def prepared_setup(setup, release_fixture):
    from plc_idf.artifacts import create_build_manifest
    from plc_idf.native_release import freeze_intent
    adapter, runner, context = setup
    project, previous_intent, _, _ = release_fixture
    artifacts = [item["path"] for item in previous_intent["manifest"]["artifacts"]]
    manifest = create_build_manifest(project, artifacts, target=context["target"],
        tool_versions={"autoshop": "offline fixture"}, source_paths=["."])
    context.update(artifacts=manifest["artifacts"], build_hash=manifest["build_hash"], source_root=str(project),
        intent=freeze_intent(manifest, operation_id=context["operation_id"], working_project=PROJECT))
    assert adapter.preflight(context, timeout_s=5)["status"] == "verified"
    return adapter, runner, context


def test_prepared_unchanged_f8_does_not_relabel_old_log_fresh(setup, release_fixture):
    adapter, runner, context = prepared_setup(setup, release_fixture)
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "completed"
    assert result["release"]["transition"]["kind"] == "unchanged-pre-confirm"
    assert result["release"]["transition"]["compile"] is None
    assert result["binary_consistency"]["status"] == "unverified"
    assert adapter.verify_download(context, result, timeout_s=5)["release_hash"] == result["release_hash"]
    assert runner.actions.count("keys") == 1 and runner.actions.count("button") == 1


@pytest.mark.parametrize("changed", ["MAIN.ST", "Config.cfg", "STTemp/MAIN.o", "steps.dat"])
def test_post_confirm_drift_preserves_release_and_vendor_result(setup, release_fixture, changed):
    project = release_fixture[0]
    if changed == "steps.dat":
        (project / changed).write_text("unclassified input that must remain bound")
    adapter, runner, context = prepared_setup(setup, release_fixture)
    def drift(action, value):
        if action == "button":
            if changed == "steps.dat":
                (project / changed).unlink()
            else:
                path = project / changed
                path.write_bytes(path.read_bytes() + b"post confirmation drift")
    runner.hook = drift
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown"
    assert result["release_hash"] == result["release"]["release_hash"]
    assert result["vendor_result"]["status"] == "verified"
    assert result["release"]["transition"]["kind"] == "unchanged-pre-confirm"
    assert runner.actions.count("button") == 1


@pytest.mark.parametrize("changed", ["MAIN.ST", "Config.cfg", "VarList.gdt"])
def test_pre_confirm_input_drift_prevents_confirmation(setup, release_fixture, changed):
    adapter, runner, context = prepared_setup(setup, release_fixture)
    def drift(action, value):
        if action == "keys":
            (release_fixture[0] / changed).write_text("unexpected native input change")
    runner.hook = drift
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown" and "input drift" in result["reason"]
    assert "button" not in runner.actions


def test_pre_confirm_recompile_without_fresh_log_is_not_confirmed(setup, release_fixture):
    adapter, runner, context = prepared_setup(setup, release_fixture)
    def drift(action, value):
        if action == "keys":
            path = release_fixture[0] / "STTemp/MAIN.o"
            path.write_bytes(path.read_bytes() + b"unproved fresh output")
    runner.hook = drift
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "unknown" and "unique fresh" in result["reason"]
    assert "button" not in runner.actions


def test_actual_pre_confirm_recompile_requires_complete_fresh_trace(setup, release_fixture):
    from .test_native_release import rewrite
    adapter, runner, context = prepared_setup(setup, release_fixture)
    runner.clock_values = ["2026-10-17T23:08:45.500000+08:00", "2026-10-17T23:08:47.500000+08:00",
                           "2026-10-17T23:08:48.500000+08:00", "2026-10-17T23:08:51.500000+08:00"]
    compile_trace = ("2026/10/17 23:08:46  开始编译\n统计-- (0)错误\n"
                     "2026/10/17 23:08:47  完成程序编译")
    def compile_or_download(action, value):
        if action == "keys":
            for artifact in context["artifacts"]:
                path = Path(artifact["path"])
                rewrite(path, path.read_bytes() + (b"revision" if path.suffix != ".rst" else b""))
            runner.log_override = compile_trace
        elif action == "button":
            runner.log_override = COMMUNICATION_DOWNLOAD.replace("23:08:46", "23:08:50")
    runner.hook = compile_or_download
    result = adapter.download(context, timeout_s=5)
    assert result["status"] == "completed", result.get("reason")
    assert result["release"]["transition"]["kind"] == "vendor-f8-recompile"
    assert result["release"]["transition"]["compile"]["error_count"] == 0
    assert result["release"]["intent_build_hash"] == context["build_hash"]


def test_prepared_timeout_never_replays_f8_or_confirmation(setup, release_fixture):
    adapter, runner, context = prepared_setup(setup, release_fixture)
    def expire(action, value):
        if action == "keys":
            raise subprocess.TimeoutExpired("offline fake F8", 1)
    runner.hook = expire
    first = adapter.download(context, timeout_s=5)
    second = adapter.download(context, timeout_s=5)
    assert first["status"] == second["status"] == "unknown"
    assert runner.actions.count("keys") == 1 and "button" not in runner.actions


def test_project_compare_rebuild_cannot_replace_prepared_release(setup, release_fixture):
    from plc_idf.native_release import verify_release
    adapter, runner, context = prepared_setup(setup, release_fixture)
    downloaded = adapter.download(context, timeout_s=5)
    assert downloaded["status"] == "completed"
    frozen_hash = downloaded["release_hash"]
    def compare_rebuild(action, value):
        if action == "click":
            path = release_fixture[0] / "STTemp/MAIN.o"
            path.write_bytes(path.read_bytes() + b"compare recompiled")
    runner.hook = compare_rebuild
    result = adapter.compare_program(context, timeout_s=5)
    assert result["status"] == "verified" and result["matches"] is True
    assert result["verification_level"] == "project" and result["project_consistency"]["status"] == "verified"
    assert result["binary_consistency"]["status"] == "unverified"
    assert result["release_hash"] == frozen_hash
    verify_release(downloaded["release"])
