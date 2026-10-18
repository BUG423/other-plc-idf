"""Fake subprocesses plus real temporary leases; never Windows/USB/PLC access."""

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "read_easy521_usb_runtime", Path(__file__).parents[1] / "read_easy521_usb_runtime.py"
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
from plc_idf.lease import LeaseBusy

BUSID = "8-3"
USB_LIST = "Connected:\nBUSID  VID:PID    DEVICE             STATE\n8-3    0471:a531  Easy521 USB        Not shared\n"


def record():
    return {
        "schema": reader.HELPER_SCHEMA, "scope": reader.HELPER_SCOPE,
        "read_profile": "literal",
        "library_sha256": reader.LIBRARY_SHA256,
        "result": {
            "ok": True, "stage": "raw-frame-only", "write_calls": 1,
            "state_semantics": "unknown", "descriptor_hex": "1201000202000040710431a5100301020001",
            "configuration_rc": 1, "claim_rc": 0,
            "request_hex": "12000101341200000100000001ff04000000",
            "bulk_write_rc": 18, "read_rcs": [14],
            "response_hex": "0e00010134120000050000000000", "release_rc": 0, "close_rc": 0,
        },
    }


class FakeRunner:
    def __init__(self, *, listing=USB_LIST, payload=None, helper_rc=0, timeout=False,
                 listing_rc=0, assert_lease=None):
        self.listing, self.payload = listing, payload if payload is not None else json.dumps(record())
        self.helper_rc, self.timeout, self.listing_rc = helper_rc, timeout, listing_rc
        self.calls = []
        self.assert_lease = assert_lease

    def __call__(self, argv, *, timeout, cwd=None):
        self.calls.append(list(argv))
        assert timeout == 25 and cwd is None
        if self.assert_lease:
            with pytest.raises(LeaseBusy):
                # A second independent open on the same resource must fail
                # even during the initial fresh usbipd enumeration.
                with reader.ResourceLease(reader.device_resource(BUSID, reader.VIDPID), directory=self.assert_lease):
                    pass
        if argv[-1] == "list":
            return SimpleNamespace(returncode=self.listing_rc, stdout=self.listing, stderr="")
        assert argv[0] == reader.POWERSHELL_WSL_PATH and "-Execute" in argv
        assert argv[argv.index("-Sequence") + 1] in ("4660", "4661")
        assert argv[argv.index("-ReadProfile") + 1] in ("literal", "gcm-state")
        assert "-File" in argv and "read_easy521_usb_runtime.ps1" in argv[argv.index("-File") + 1]
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout, output="partial raw evidence")
        return SimpleNamespace(returncode=self.helper_rc, stdout=self.payload, stderr="")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    helper = tmp_path / "read_easy521_usb_runtime.ps1"
    helper.write_text("# fake helper; never run\n")
    monkeypatch.setattr(reader, "runtime_asset", lambda root, relative: helper)
    monkeypatch.setattr(reader.secrets, "randbits", lambda bits: 0x1234)
    return tmp_path / "leases", helper


def execute(environment, runner, **kwargs):
    directory, _ = environment
    return reader.read_runtime(execute=True, busid=kwargs.pop("busid", BUSID),
                               lease_directory=directory, runner=runner, **kwargs)


def test_preview_never_resolves_helper_runs_process_or_acquires_lease(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preview attempted an external/lease action")
    monkeypatch.setattr(reader, "runtime_asset", forbidden)
    monkeypatch.setattr(reader, "ResourceLease", forbidden)
    monkeypatch.setattr(reader.secrets, "randbits", forbidden)
    result = reader.read_runtime(runner=forbidden)
    assert result["ok"] and result["status"] == "preview" and not result["device_access"]


def test_correlated_raw_zero_is_observed_without_state_or_identity_claim(environment):
    fake = FakeRunner(assert_lease=environment[0])
    result = execute(environment, fake)
    assert result["ok"] and result["status"] == "observed" and result["raw_value"] == 0
    assert result["state_semantics"] == "unknown"
    assert result["cpu_state_verified"] is False and result["cpu_identity_verified"] is False
    assert result["application_execution_verified"] is False and result["linux_native_usb"] is False
    assert len(fake.calls) == 2 and result["raw_transport"]["attempts"] == 1
    assert result["raw_transport"]["stdout"] == fake.payload
    # Both calls completed under the lock, and the lock is now available again.
    with reader.ResourceLease(reader.device_resource(BUSID, reader.VIDPID), directory=environment[0]):
        pass


@pytest.mark.parametrize("busid", [None, "", "6-4;whoami", "stale", 83, True])
def test_explicit_valid_busid_required_before_any_windows_call(environment, busid):
    fake = FakeRunner()
    result = execute(environment, fake, busid=busid)
    assert not result["ok"] and result["stage"] == "preflight" and not fake.calls


@pytest.mark.parametrize("listing", [
    USB_LIST.replace("8-3", "6-4"),
    USB_LIST + "8-4    0471:a531  Another PLC        Not shared\n",
    USB_LIST + "8-3    1234:5678  Ambiguous port     Not shared\n",
    USB_LIST.replace("Not shared", "Attached"),
    USB_LIST.replace("Not shared", "Shared"),
    USB_LIST.replace("0471:a531", "1234:5678"),
    "not a device table",
])
def test_stale_ambiguous_shared_attached_or_missing_device_never_calls_helper(environment, listing):
    fake = FakeRunner(listing=listing)
    result = execute(environment, fake)
    assert not result["ok"] and len(fake.calls) == 1 and not result["device_access"]


def test_busy_lease_prevents_even_initial_enumeration(environment):
    fake = FakeRunner()
    with reader.ResourceLease(reader.device_resource(BUSID, reader.VIDPID), directory=environment[0]):
        result = execute(environment, fake)
    assert not result["ok"] and result["stage"] == "lease" and not fake.calls


def test_missing_helper_fails_without_windows_calls(environment):
    environment[1].unlink()
    fake = FakeRunner()
    result = execute(environment, fake)
    assert not result["ok"] and not fake.calls


def test_listing_failure_never_runs_helper(environment):
    fake = FakeRunner(listing_rc=1)
    result = execute(environment, fake)
    assert not result["ok"] and len(fake.calls) == 1


def test_helper_timeout_is_one_attempt_and_keeps_outcome_unknown(environment):
    fake = FakeRunner(timeout=True)
    result = execute(environment, fake)
    assert not result["ok"] and result["stage"] == "transport"
    assert result["request_outcome"] == "unknown" and len(fake.calls) == 2
    assert result["raw_transport"]["timed_out"] and result["raw_transport"]["attempts"] == 1
    assert result["raw_transport"]["stdout"] == "partial raw evidence"


@pytest.mark.parametrize("payload", ["verified", "{}", "[]", '{"schema":1,"schema":2}',
                                      '{"ok":NaN}', json.dumps(record()) + "\n{}"])
def test_printed_tokens_duplicate_keys_and_invalid_json_never_count_as_observation(environment, payload):
    fake = FakeRunner(payload=payload)
    result = execute(environment, fake)
    assert not result["ok"] and result["raw_transport"]["stdout"] == payload
    assert len(fake.calls) == 2


@pytest.mark.parametrize("field,value", [
    ("ok", 1), ("stage", "verified"), ("state_semantics", "RUN"),
    ("write_calls", 2), ("write_calls", True), ("bulk_write_rc", 17),
    ("claim_rc", -1), ("configuration_rc", 0), ("release_rc", -1),
    ("close_rc", -1), ("close_rc", False), ("read_rcs", []),
    ("read_rcs", [True, 13]), ("read_rcs", [13]),
    ("request_hex", "12000101341200000200000001ff04000000"),
    ("descriptor_hex", "120100020200004034127856100301020001"),
    ("response_hex", "0e00010135120000050000000000"),  # Wrong sequence.
    ("response_hex", "0e00010134120000060000000000"),  # Ack, not data.
    ("response_hex", "0e000101341200000f0001000000"),  # Error code 1.
    ("response_hex", "0e000101341200000f0000000000"),  # Error code 0.
    ("response_hex", "0e0001013412000005000000"),      # Truncation.
])
def test_transport_fields_and_frame_fail_closed_without_retry(environment, field, value):
    data = record(); data["result"][field] = value
    fake = FakeRunner(payload=json.dumps(data))
    result = execute(environment, fake)
    assert not result["ok"] and result["stage"] == "validate" and len(fake.calls) == 2
    assert "raw_value" not in result


@pytest.mark.parametrize("field,value", [("schema", "other"), ("scope", "verified native Linux"),
                                          ("library_sha256", "0" * 64)])
def test_helper_outer_identity_is_checked(environment, field, value):
    data = record(); data[field] = value
    assert not execute(environment, FakeRunner(payload=json.dumps(data)))["ok"]


def test_nonzero_helper_exit_cannot_be_overridden_by_valid_success_json(environment):
    result = execute(environment, FakeRunner(helper_rc=1))
    assert not result["ok"] and result["stage"] == "transport"


def test_main_preview_is_single_json_and_zero_exit(capsys):
    assert reader.main([]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "preview" and output.err == ""


def test_main_execute_without_busid_is_single_failure_json(capsys):
    assert reader.main(["--execute"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_main_unknown_option_is_one_json_without_argparse_stderr(capsys):
    assert reader.main(["--arbitrary-frame", "ff"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["stage"] == "arguments" and output.err == ""


def test_main_interrupt_is_nonzero_and_does_not_report_success(capsys, monkeypatch):
    def interrupted(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(reader, "read_runtime", interrupted)
    assert reader.main([]) == 130
    assert json.loads(capsys.readouterr().out)["request_outcome"] == "unknown"


def test_new_execution_sequence_rejects_old_calls_late_response(environment, monkeypatch):
    first = execute(environment, FakeRunner())
    assert first["ok"] and first["sequence"] == 0x1234
    monkeypatch.setattr(reader.secrets, "randbits", lambda bits: 0x1235)
    data = record()
    # Helper sent the NEW sequence, but received the old call's valid frame.
    data["result"]["request_hex"] = "12000101351200000100000001ff04000000"
    fake = FakeRunner(payload=json.dumps(data))
    second = execute(environment, fake)
    assert not second["ok"] and second["stage"] == "validate"
    assert second["sequence"] == 0x1235 and len(fake.calls) == 2
    assert "response sequence differs" in second["diagnostics"][0]["message"]


def test_success_with_new_sequence_is_bound_to_exact_sent_request(environment, monkeypatch):
    monkeypatch.setattr(reader.secrets, "randbits", lambda bits: 0x1235)
    data = record()
    data["result"]["request_hex"] = "12000101351200000100000001ff04000000"
    data["result"]["response_hex"] = "0e00010135120000050000000000"
    result = execute(environment, FakeRunner(payload=json.dumps(data)))
    assert result["ok"] and result["decoded"]["sequence"] == 0x1235
    assert result["request_hex"] == data["result"]["request_hex"]
    assert result["helper"]["sha256_after"] == result["helper"]["sha256"]


def test_helper_change_during_execution_is_failed_unknown_with_raw_evidence(environment):
    fake = FakeRunner()
    def changed(argv, **kwargs):
        response = fake(argv, **kwargs)
        if "-Execute" in argv:
            environment[1].write_text("# changed while helper was running\n")
        return response
    result = execute(environment, changed)
    assert not result["ok"] and result["stage"] == "helper_integrity"
    assert result["request_outcome"] == "unknown" and "raw_value" not in result
    assert result["raw_transport"]["stdout"] == fake.payload
    assert result["helper"]["sha256_after"] != result["helper"]["sha256"]
    assert len(fake.calls) == 2


@pytest.mark.parametrize("value,mapped", [(0, "STOP"), (1, "RUN"), (2, "unknown"), (3, "unknown")])
def test_gcm_has_own_exact_one_byte_profile_and_only_static_state_mapping(environment, value, mapped):
    data = record(); data["read_profile"] = "gcm-state"
    data["result"].update(request_hex="12000101341200000100000000ff01000000",
                          response_hex="0b000101341200000500" + bytes([value]).hex(), read_rcs=[11])
    fake = FakeRunner(payload=json.dumps(data))
    result = execute(environment, fake, profile="gcm-state")
    assert result["ok"] and result["raw_value"] == value
    assert result["static_cpu_state_interpretation"]["value"] == mapped
    assert result["static_cpu_state_interpretation"]["device_calibrated"] is False
    assert result["cpu_state_verified"] is False and result["state_semantics"] == "unknown"
    assert fake.calls[1][-2:] == ["-ReadProfile", "gcm-state"]


def test_helper_profile_cannot_silently_select_different_index(environment):
    data = record(); data["read_profile"] = "gcm-state"
    result = execute(environment, FakeRunner(payload=json.dumps(data)))
    assert not result["ok"] and result["stage"] == "validate"


@pytest.mark.parametrize("profile", ["runtime", "0xff000000", True, []])
def test_unknown_profile_is_rejected_before_windows_even_for_preview(profile):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid profile accessed Windows")
    result = reader.read_runtime(profile=profile, runner=forbidden)
    assert not result["ok"] and result["stage"] == "arguments"


def test_gcm_preview_does_not_run_windows(capsys):
    assert reader.main(["--profile", "gcm-state"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preview" and result["read_profile"] == "gcm-state"
    assert result["request_hex"] == "12000101341200000100000000ff01000000"
