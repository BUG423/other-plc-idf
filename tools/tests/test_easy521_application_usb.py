"""Application USB C-ABI substitutes only; no USB, Windows or PLC is accessed."""

import importlib.util
import hashlib
from pathlib import Path
import struct

import pytest

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

base = module("application_usb_fake_base", "test_easy521_linux_usb.py")
spec = importlib.util.spec_from_file_location("application_usb", Path(__file__).parents[1] / "easy521_application_usb.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FileUsb(base.FakeLibusb):
    def __init__(self, *, content=b"abc", failure_at=None, error_code=None, mutation=None,
                 out_status=None, out_count=None, in_status=None, in_partial=b"",
                 zlp_after_full=False, zlp_count=1, on_zlp=None, **kwargs):
        super().__init__(**kwargs)
        self.content, self.failure_at, self.error_code, self.mutation = content, failure_at, error_code, mutation
        self.blocks, self.opened = [], False
        self.out_status, self.out_count, self.in_status, self.in_partial = out_status, out_count, in_status, in_partial
        self.zlp_after_full, self.zlp_count, self.pending_zlp = zlp_after_full, zlp_count, 0
        self.on_zlp = on_zlp

    def _bulk_transfer(self, handle, endpoint, data, length, transferred, timeout):
        assert 0 < timeout <= 10000
        if endpoint == 0x02:
            self.sent.append(bytes(data[:length]))
            if self.out_status is not None:
                base.set_pointer(transferred, base.C.c_int, length if self.out_count is None else self.out_count)
                return self.out_status
            base.set_pointer(transferred, base.C.c_int, 5 if len(self.sent) == self.failure_at else length)
            return -7 if len(self.sent) == self.failure_at else 0
        assert endpoint == 0x81 and length == 512
        if self.pending_zlp:
            self.pending_zlp -= 1
            base.set_pointer(transferred, base.C.c_int, 0)
            self.received += 1
            if self.on_zlp: self.on_zlp()
            return 0
        if self.in_status is not None:
            for index, byte in enumerate(self.in_partial): data[index] = byte
            base.set_pointer(transferred, base.C.c_int, len(self.in_partial))
            self.received += 1
            return self.in_status
        request = self.sent[-1]
        metadata = app.codec.parse_request(request)
        if self.error_code is not None:
            operation, body = 15, struct.pack("<I", self.error_code)
        elif metadata["action"] == "file-open-read":
            self.opened = True
            operation, body = 6, struct.pack("<I", len(self.content))
        elif metadata["action"] == "file-read":
            assert self.opened
            block = metadata["block"]
            self.blocks.append(block)
            chunk = self.content[block * 494:(block + 1) * 494]
            operation, body = 5, struct.pack("<II", block, len(chunk)) + chunk
        else:
            operation, body = 6, struct.pack("<I", 0)
        frame = bytearray(struct.pack("<HBBHHBB", 10 + len(body), 1, request[3],
                                     int.from_bytes(request[4:6], "little"), 0, operation, 0) + body)
        if self.mutation == "sequence": frame[4] ^= 1
        if self.mutation == "count" and operation == 5: frame[14] ^= 1
        for index, byte in enumerate(frame): data[index] = byte
        base.set_pointer(transferred, base.C.c_int, len(frame))
        self.received += 1
        if self.zlp_after_full and len(frame) == 512:
            self.pending_zlp = self.zlp_count
        return 0


def upload(tmp_path, fake=None, **kwargs):
    fake = FileUsb() if fake is None else fake
    return app.upload_native(remote_name="stprog.o", credential=b"\0" * 8,
                             execute=True, linux_device="001:004", output_path=tmp_path / "backup.o",
                             lease_directory=tmp_path / "leases",
                             backend_factory=lambda: app.native.Libusb1Transport(library=fake), **kwargs)


def test_preview_never_loads_library_opens_output_or_acquires_lease(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("preview accessed a resource")
    monkeypatch.setattr(app.native.C, "CDLL", forbidden)
    monkeypatch.setattr(app.native, "ResourceLease", forbidden)
    monkeypatch.setattr(app.Path, "exists", forbidden)
    result = app.upload_native(remote_name="stprog.o", credential=b"\0" * 8, backend_factory=forbidden)
    assert result["ok"] and result["status"] == "preview" and not result["device_access"]
    assert app.set_gcm_native(state=0, backend_factory=forbidden)["status"] == "preview"


@pytest.mark.parametrize("size", [0, 1, 494, 495, 988, 1001])
def test_one_handle_reads_exact_blocks_and_saves_only_verified_complete_file(tmp_path, size):
    content = bytes(index % 251 for index in range(size))
    fake = FileUsb(content=content)
    result = upload(tmp_path, fake)
    assert result["ok"] and result["bytes"] == size and (tmp_path / "backup.o").read_bytes() == content
    assert fake.names.count("claim_interface") == fake.names.count("release_interface") == 1
    assert len(fake.sent) == 1 + (size + 493) // 494
    assert [app.codec.parse_request(frame)["action"] for frame in fake.sent] == ["file-open-read"] + ["file-read"] * len(fake.blocks)
    assert fake.blocks == list(range((size + 493) // 494))
    assert result["plc_change_possible"] is False and not result["complete_application_verified"]
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


@pytest.mark.parametrize("options", [{"kernel": 1}, {"claim": -6}, {"open_rc": -3}, {"targets": 2}])
def test_busy_inaccessible_or_ambiguous_device_never_sends_file_open(tmp_path, options):
    fake = FileUsb(**options)
    result = upload(tmp_path, fake)
    assert not result["ok"] and not fake.sent and not (tmp_path / "backup.o").exists()


@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_partial_send_never_replays_and_keeps_unknown(tmp_path, failure_at):
    fake = FileUsb(content=b"a" * 1000, failure_at=failure_at)
    result = upload(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert len(fake.sent) == failure_at and result["requests"][-1]["out_transferred"] == 5
    assert not (tmp_path / "backup.o").exists()
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


@pytest.mark.parametrize("code", [0, 1, 0x41, 0x84])
def test_device_error_stops_after_one_request_without_filename_fallback(tmp_path, code):
    fake = FileUsb(error_code=code)
    result = upload(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "device_error_response"
    assert len(fake.sent) == 1 and not (tmp_path / "backup.o").exists()


@pytest.mark.parametrize("mutation", ["sequence", "count"])
def test_uncorrelated_or_short_block_never_saves_backup(tmp_path, mutation):
    fake = FileUsb(mutation=mutation)
    result = upload(tmp_path, fake)
    assert not result["ok"] and not (tmp_path / "backup.o").exists()


def test_remote_size_limit_does_not_allocate_or_request_blocks(tmp_path):
    fake = FileUsb(content=b"x" * 100)
    result = upload(tmp_path, fake, max_file_bytes=99)
    assert not result["ok"] and len(fake.sent) == 1 and not fake.blocks


def test_512_byte_response_allows_one_leading_zlp_before_next_correlated_block(tmp_path):
    fake = FileUsb(content=b"x" * 1001, zlp_after_full=True)
    result = upload(tmp_path, fake)
    assert result["ok"] and (tmp_path / "backup.o").read_bytes() == fake.content
    assert len(fake.sent) == 4  # open + three distinct reads; no replay OUT.
    lengths = [[item["transferred"] for item in request["read_calls"]] for request in result["requests"]]
    assert lengths == [[14], [512], [0, 512], [0, 31]]
    assert all(request["write_calls"] == 1 for request in result["requests"])
    for index in (2, 3):
        assert result["requests"][index]["read_calls"][0]["accepted_prior_full_packet_zlp"] is True


def test_zlp_without_previous_full_packet_response_is_rejected(tmp_path):
    fake = FileUsb(in_status=0)
    result = upload(tmp_path, fake)
    assert not result["ok"] and len(fake.sent) == 1 and fake.received == 1
    assert "accepted_prior_full_packet_zlp" not in result["requests"][0]["read_calls"][0]


def test_second_zlp_exceeds_single_preceding_packet_allowance(tmp_path):
    fake = FileUsb(content=b"x" * 1001, zlp_after_full=True, zlp_count=2)
    result = upload(tmp_path, fake)
    assert not result["ok"] and len(fake.sent) == 3 and fake.blocks == [0]
    assert [item["transferred"] for item in result["requests"][-1]["read_calls"]] == [0, 0]
    assert result["request_outcome"] == "unknown" and not (tmp_path / "backup.o").exists()


def test_zlp_consumes_existing_receive_deadline_without_extending_budget(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    def advance(): clock[0] += 2.501
    fake = FileUsb(content=b"x" * 1001, zlp_after_full=True, on_zlp=advance)
    result = upload(tmp_path, fake)
    assert not result["ok"] and len(fake.sent) == 3 and fake.blocks == [0]
    assert len(result["requests"][-1]["read_calls"]) == 1
    assert result["requests"][-1]["read_calls"][0]["accepted_prior_full_packet_zlp"]


def test_existing_local_output_is_never_overwritten_and_prevents_device_access(tmp_path):
    (tmp_path / "backup.o").write_bytes(b"old")
    fake = FileUsb()
    result = upload(tmp_path, fake)
    assert not result["ok"] and not fake.calls and (tmp_path / "backup.o").read_bytes() == b"old"


def test_failed_release_prevents_local_backup_acceptance(tmp_path):
    fake = FileUsb(release=-1)
    result = upload(tmp_path, fake)
    assert not result["ok"] and not (tmp_path / "backup.o").exists()


@pytest.mark.parametrize("partial", [b"", bytes.fromhex("0e000104")])
def test_in_timeout_preserves_partial_or_zero_bytes_without_replay(tmp_path, partial):
    fake = FileUsb(in_status=-7, in_partial=partial)
    result = upload(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert len(fake.sent) == 1 and fake.received == 1
    assert result["requests"][0]["response_hex"] == partial.hex()
    assert result["requests"][0]["read_calls"] == [{"rc": -7, "transferred": len(partial)}]
    assert not (tmp_path / "backup.o").exists()


@pytest.mark.parametrize("status,count", [(-7, 0), (-7, 5), (0, 0), (0, 5)])
def test_out_timeout_or_short_success_even_zero_bytes_is_unknown_and_not_replayed(tmp_path, status, count):
    fake = FileUsb(out_status=status, out_count=count)
    result = upload(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert len(fake.sent) == 1 and fake.received == 0
    assert result["requests"][0]["out_rc"] == status and result["requests"][0]["out_transferred"] == count


def test_cli_interrupt_preserves_actual_device_report_partial_response_and_cleanup(tmp_path, monkeypatch, capsys):
    import json
    fake = FileUsb()
    original = fake.libusb_bulk_transfer.implementation
    def interrupted(*args):
        if args[1] == 0x81:
            raise KeyboardInterrupt()
        return original(*args)
    fake.libusb_bulk_transfer.implementation = interrupted
    monkeypatch.setattr(app.native, "Libusb1Transport", lambda: base.usb.Libusb1Transport(library=fake))
    monkeypatch.setattr(app.native, "ResourceLease", lambda *args, **kwargs: base.usb.ResourceLease("interrupt-test", directory=tmp_path / "leases"))
    code = app.main(["--remote-name", "stprog.o", "--credential-hex", "0000000000000000",
                     "--execute", "--linux-device", "001:004", "--output-path", str(tmp_path / "backup.o")])
    report = json.loads(capsys.readouterr().out)
    assert code == 130 and report["status"] == "interrupted" and report["device_access"] is True
    assert report["request_outcome"] == "unknown" and report["interrupted_at_stage"] == "receive"
    assert len(report["requests"]) == 1 and report["requests"][0]["write_calls"] == 1
    assert report["requests"][0]["out_transferred"] == len(fake.sent[0])
    assert report["release_rc"] == 0 and report["close_called"] is True
    assert not (tmp_path / "backup.o").exists()


def test_read_only_session_refuses_control_writer_before_out(tmp_path):
    fake = FileUsb()
    report = app._report("test")
    with app._Session(device="001:004", timeout_ms=2500, deadline=app.time.monotonic() + 30,
                      report=report, backend_factory=lambda: app.native.Libusb1Transport(library=fake)) as session:
        with pytest.raises(app.native.NativeUsbError):
            session.exchange(app.codec.build_gcm_set(state=0, sequence=0))
    assert not fake.sent


def test_control_api_sends_one_exact_write_and_does_not_claim_state_readback(tmp_path):
    fake = FileUsb()
    result = app.set_gcm_native(state=0, execute=True, linux_device="001:004",
                               lease_directory=tmp_path,
                               backend_factory=lambda: app.native.Libusb1Transport(library=fake))
    assert result["ok"] and result["status"] == "control-acknowledged" and result["plc_change_possible"]
    assert len(fake.sent) == 1 and app.codec.parse_request(fake.sent[0])["state"] == 0
    assert not result["cpu_state_verified"] and result["ack_u32"] == 0


def records(*files):
    return [{"remote_name": name, "data": data, "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files]


def download(tmp_path, fake=None, **kwargs):
    fake = FileUsb() if fake is None else fake
    return app.download_native(files=kwargs.pop("files", records(("stprog.o", b"abc"))),
                               credential=b"\0" * 8, source_sha256="1" * 64, build_sha256="2" * 64,
                               execute=kwargs.pop("execute", True), linux_device="001:004",
                               lease_directory=tmp_path / "leases",
                               backend_factory=lambda: app.native.Libusb1Transport(library=fake), **kwargs)


def test_explicit_download_orders_control_and_data_but_never_claims_activation(tmp_path):
    fake = FileUsb()
    data = b"x" * 40960 + b"abc"
    result = download(tmp_path, fake, files=records(("stprog.o", data), ("userprog.foid", b"foid")))
    assert result["ok"] and result["status"] == "transfer-acknowledged"
    decoded = [app.codec.parse_request(frame) for frame in fake.sent]
    assert [item["action"] for item in decoded] == ["gcm-set", "file-open-write", "file-write",
        "file-write", "file-open-write", "file-write", "gcm-set"]
    assert decoded[0]["state"] == 2 and decoded[-1]["state"] == 3
    assert [decoded[index]["block"] for index in (2, 3, 5)] == [0, 1, 0]
    assert b"".join(fake.sent[index][18:] for index in (2, 3)) == data
    assert result["plc_change_possible"] and not result["application_closure_verified"]
    assert not result["activation_verified"] and not result["application_execution_verified"]
    assert fake.names.count("claim_interface") == 1


@pytest.mark.parametrize("failure_at", range(1, 5))
def test_download_failure_stops_without_commit_run_retry_or_rollback(tmp_path, failure_at):
    fake = FileUsb(failure_at=failure_at)
    result = download(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert len(fake.sent) == failure_at and result["plc_change_possible"]
    # For this one-file example the final raw3 is request4. A failed earlier
    # request must not cause raw3/STOP/RUN/rollback to be sent afterward.
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "empty", "path", "mutable", "fields"])
def test_invalid_download_manifest_prevents_all_usb_access(tmp_path, mutation):
    items = records(("stprog.o", b"abc"))
    if mutation == "hash": items[0]["sha256"] = "0" * 64
    elif mutation == "duplicate": items.append(dict(items[0]))
    elif mutation == "empty": items = []
    elif mutation == "path": items[0]["remote_name"] = "../stprog.o"
    elif mutation == "mutable": items[0]["data"] = bytearray(b"abc")
    elif mutation == "fields": items[0]["optional"] = True
    fake = FileUsb()
    assert not download(tmp_path, fake, files=items)["ok"] and not fake.calls


def test_download_preview_never_loads_library_or_claims_device(tmp_path):
    fake = FileUsb()
    result = download(tmp_path, fake, execute=False)
    assert result["ok"] and result["status"] == "preview" and not fake.calls
