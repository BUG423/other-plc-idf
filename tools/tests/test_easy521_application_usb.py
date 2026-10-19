"""Application USB C-ABI substitutes only; no USB, Windows or PLC is accessed."""

import importlib.util
import hashlib
import json
import os
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


def candidate(tmp_path, *, live=False):
    """Synthetic local manifest provenance, not a valid device application."""
    root = tmp_path / "candidate"
    root.mkdir()
    names = app._LIVE_FILES if live else app._FIXED_FILES
    files = []
    for index, name in enumerate(names):
        data = b"Empty" if name == "project.hcpp" else name.encode()
        path = root / name
        path.write_bytes(data)
        files.append({"index": index, "remote_name": name, "path": str(path), "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "origin": "test-only-unverified"})
    association = {"source.st": "1" * 64, **{x["remote_name"]: x["sha256"] for x in files
                                            if x["remote_name"] in ("stprog.o", "stuserprog.o")}}
    if live:
        manifest = {"schema": "plcidf.easy521-live-application-inspection.v1",
                    "profile": "easy521-observed-live-interface-v1", "status": "live-derived-candidate-prepared",
                    "source_association": association, "project_archive_included": False,
                    "archive_transfer_policy_verified": False,
                    "readback_target": {"linux_device": "001:004", "vidpid": "0471:a531", "serial": None}}
    else:
        manifest = {"schema": "plcidf.easy521-fixed-application-candidate.v1",
                    "profile": "easy521-bess-existing-interface-v1", "status": "candidate-prepared",
                    "association": association, "source_recovery_archive": "vendor-Empty-marker"}
    manifest.update(ok=True, file_count=len(files), files=files, downloadable=False, runtime_abi_verified=False,
                    device_accepted=False, device_executed=False, symbol_metadata_inherited="test-opaque",
                    inputs=[{"snapshot_path": "/must/not/be/opened", "sha256": "2" * 64}])
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


@pytest.mark.parametrize("live", [False, True])
def test_manifest_preview_snapshots_fixed_order_without_device_library_lease_random_or_writes(tmp_path, monkeypatch, live):
    path, manifest = candidate(tmp_path, live=live)
    def forbidden(*args, **kwargs): pytest.fail("preview accessed USB/lease/random")
    monkeypatch.setattr(app.native.C, "CDLL", forbidden)
    monkeypatch.setattr(app.native, "ResourceLease", forbidden)
    monkeypatch.setattr(app.secrets, "randbits", forbidden)
    before = {p: p.read_bytes() for p in path.parent.iterdir()}
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8,
                                          linux_device="001:004")
    assert result["ok"] and result["status"] == "preview" and result["device_access"] is False
    assert result["source_sha256"] == "1" * 64 and result["build_sha256"] == hashlib.sha256(before[path]).hexdigest()
    assert result["build_sha256_kind"] == "candidate-manifest"
    assert result["candidate_metadata"]["downloadable"] is False
    assert result["candidate_metadata"]["runtime_abi_verified"] is False
    assert result["candidate_metadata"]["symbol_metadata_inherited"] == "test-opaque"
    assert result["candidate_provenance_inputs_reopened"] is False
    assert result["permanent_target_identity"] == "unknown" and result["local_snapshot_rechecked"]
    assert [x["remote_name"] for x in result["files"]] == list(app._LIVE_FILES if live else app._FIXED_FILES)
    assert {p: p.read_bytes() for p in path.parent.iterdir()} == before


@pytest.mark.parametrize("mutation", ["schema", "profile", "status", "count", "index", "order", "name", "size", "hash",
                                       "association", "source", "archive", "missing", "duplicate-json", "nan", "oversize-manifest"])
def test_invalid_candidate_manifest_never_invokes_low_level_writer(tmp_path, monkeypatch, mutation):
    path, manifest = candidate(tmp_path)
    if mutation == "schema": manifest["schema"] = "other"
    elif mutation == "profile": manifest["profile"] = "other"
    elif mutation == "status": manifest["status"] = "inspected"
    elif mutation == "count": manifest["file_count"] = 17
    elif mutation == "index": manifest["files"][0]["index"] = False
    elif mutation == "order": manifest["files"][0], manifest["files"][1] = manifest["files"][1], manifest["files"][0]
    elif mutation == "name": manifest["files"][0]["remote_name"] = "../project.hcpp"
    elif mutation == "size": manifest["files"][0]["size"] += 1
    elif mutation == "hash": manifest["files"][0]["sha256"] = "0" * 64
    elif mutation == "association": manifest["association"]["stprog.o"] = "0" * 64
    elif mutation == "source": manifest["association"]["source.st"] = "UNKNOWN"
    elif mutation == "archive":
        target = Path(manifest["files"][0]["path"])
        target.write_bytes(b"Other")
        manifest["files"][0]["sha256"] = hashlib.sha256(b"Other").hexdigest()
    elif mutation == "missing": Path(manifest["files"][1]["path"]).unlink()
    path.write_text(json.dumps(manifest))
    if mutation == "duplicate-json": path.write_text('{"ok":true,"ok":false}')
    elif mutation == "nan": path.write_text('{"ok":NaN}')
    elif mutation == "oversize-manifest": path.write_bytes(b" " * (1024 * 1024 + 1))
    def forbidden(**kwargs): pytest.fail("invalid candidate reached writer")
    monkeypatch.setattr(app, "download_native", forbidden)
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8,
                                          execute=True, linux_device="001:004")
    assert not result["ok"] and result["device_access"] is False and result["request_outcome"] == "not_sent"


@pytest.mark.parametrize("target", ["001:005", "bad"])
def test_live_candidate_location_mismatch_rejected_before_low_level_writer(tmp_path, monkeypatch, target):
    path, _ = candidate(tmp_path, live=True)
    monkeypatch.setattr(app, "download_native", lambda **kwargs: pytest.fail("wrong target reached writer"))
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8,
                                          execute=True, linux_device=target)
    assert not result["ok"] and result["device_access"] is False


@pytest.mark.parametrize("which,kind", [("manifest", "symlink"), ("payload", "symlink"), ("payload", "fifo"), ("payload", "directory")])
def test_candidate_symlink_or_nonregular_input_cannot_block_or_reach_writer(tmp_path, monkeypatch, which, kind):
    path, manifest = candidate(tmp_path)
    selected = path if which == "manifest" else Path(manifest["files"][1]["path"])
    raw = selected.read_bytes()
    selected.unlink()
    if kind == "symlink":
        other = tmp_path / "other"
        other.write_bytes(raw)
        selected.symlink_to(other)
    elif kind == "fifo": os.mkfifo(selected)
    else: selected.mkdir()
    monkeypatch.setattr(app, "download_native", lambda **kwargs: pytest.fail("nonregular reached writer"))
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8)
    assert not result["ok"] and not result.get("transaction_invoked", False)


@pytest.mark.parametrize("which", ["manifest", "payload"])
def test_candidate_input_drift_during_final_recheck_is_rejected(tmp_path, monkeypatch, which):
    path, manifest = candidate(tmp_path)
    selected = path if which == "manifest" else Path(manifest["files"][1]["path"])
    original = app._candidate_read
    counts = {}
    def drift(file_path, **kwargs):
        counts[file_path] = counts.get(file_path, 0) + 1
        if file_path == selected and counts[file_path] == 2:
            selected.write_bytes(selected.read_bytes() + b" ")
        return original(file_path, **kwargs)
    monkeypatch.setattr(app, "_candidate_read", drift)
    monkeypatch.setattr(app, "download_native", lambda **kwargs: pytest.fail("drift reached writer"))
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8)
    assert not result["ok"] and result["stage"] == "snapshot-recheck"


def test_candidate_execute_passes_one_immutable_snapshot_and_keeps_ack_separate_from_acceptance(tmp_path, monkeypatch):
    path, manifest = candidate(tmp_path)
    calls = []
    def writer(**kwargs):
        calls.append(kwargs)
        Path(manifest["files"][1]["path"]).write_bytes(b"changed-after-snapshot")
        return {**app._report("download-files"), "ok": True, "status": "transfer-acknowledged",
                "device_access": True, "request_outcome": "all_transfer_requests_acknowledged"}
    monkeypatch.setattr(app, "download_native", writer)
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8,
                                          execute=True, linux_device="001:004", timeout_ms=2222)
    assert len(calls) == 1 and calls[0]["execute"] is True and calls[0]["timeout_ms"] == 2222
    assert calls[0]["source_sha256"] == "1" * 64 and calls[0]["build_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert calls[0]["files"][1]["data"] == b"userprog.foid" and type(calls[0]["files"][1]["data"]) is bytes
    assert result["ok"] and result["status"] == "transfer-acknowledged" and result["device_access"]
    assert result["candidate_metadata"]["downloadable"] is False and not result["application_execution_verified"]


@pytest.mark.parametrize("action,state", [("run", 1), ("stop", 0)])
def test_control_cli_default_preview_keeps_mapping_and_zero_device_access(monkeypatch, capsys, action, state):
    def forbidden(*args, **kwargs): pytest.fail("preview accessed resource")
    monkeypatch.setattr(app.native.C, "CDLL", forbidden)
    monkeypatch.setattr(app.native, "ResourceLease", forbidden)
    monkeypatch.setattr(app.secrets, "randbits", forbidden)
    assert app.main([action]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "preview" and report["requested_state"] == state and report["device_access"] is False


def test_legacy_read_cli_preview_does_not_inspect_output(monkeypatch, capsys):
    def forbidden(*args, **kwargs): pytest.fail("read preview inspected a path")
    monkeypatch.setattr(app.Path, "exists", forbidden)
    monkeypatch.setattr(app.Path, "lstat", forbidden)
    assert app.main(["--remote-name", "stprog.o", "--credential-hex", "0000000000000000", "--output-path", "/unreadable/path"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "preview"


def test_manifest_cli_execute_interrupt_preserves_low_level_evidence_without_retry(tmp_path, monkeypatch, capsys):
    path, _ = candidate(tmp_path, live=True)
    calls = []
    def interrupted(**kwargs):
        calls.append(kwargs)
        return {**app._report("download-files"), "status": "interrupted", "device_access": True,
                "stage": "interrupted", "interrupted_at_stage": "receive", "plc_change_possible": True,
                "request_outcome": "unknown", "release_rc": 0, "close_called": True,
                "requests": [{"write_calls": 1, "out_transferred": 20, "request_outcome": "unknown"}]}
    monkeypatch.setattr(app, "download_native", interrupted)
    code = app.main(["download", "--manifest", str(path), "--credential-hex", "0000000000000000",
                     "--execute", "--linux-device", "001:004"])
    report = json.loads(capsys.readouterr().out)
    assert code == 130 and len(calls) == 1 and report["device_access"] is True
    assert report["requests"][0]["out_transferred"] == 20 and report["request_outcome"] == "unknown"
    assert report["release_rc"] == 0 and report["close_called"] is True
    assert report["candidate_metadata"]["runtime_abi_verified"] is False


def test_unexpected_writer_interrupt_does_not_invent_no_device_access(tmp_path, monkeypatch):
    path, _ = candidate(tmp_path)
    def interrupted(**kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr(app, "download_native", interrupted)
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8, execute=True, linux_device="001:004")
    assert result["status"] == "interrupted" and result["device_access"] is None and result["request_outcome"] == "unknown"
    assert result["plc_change_possible"] is True


def test_manifest_total_snapshot_budget_rejects_before_fourth_large_payload(tmp_path, monkeypatch):
    path, manifest = candidate(tmp_path, live=True)
    block = b"x" * (8 * 1024 * 1024)
    digest = hashlib.sha256(block).hexdigest()
    for record in manifest["files"][:5]:
        record.update(size=len(block), sha256=digest)
    manifest["source_association"].update({name: digest for name in ("stprog.o", "stuserprog.o")})
    path.write_text(json.dumps(manifest))
    original = app._candidate_read
    large_reads = []
    def synthetic_large(file_path, **kwargs):
        if file_path != path:
            large_reads.append(file_path.name)
            return block, (0, 0, len(block), 0, 0)
        return original(file_path, **kwargs)
    monkeypatch.setattr(app, "_candidate_read", synthetic_large)
    monkeypatch.setattr(app, "download_native", lambda **kwargs: pytest.fail("oversize reached writer"))
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8)
    assert not result["ok"] and "32MiB" in result["diagnostics"][0]["message"]
    # The manifest itself consumes budget, so three8MiB payloads fit, four do not.
    assert len(large_reads) == 3


def test_snapshot_refuses_fifo_substitution_between_lstat_and_open(tmp_path, monkeypatch):
    path, manifest = candidate(tmp_path)
    selected = Path(manifest["files"][1]["path"])
    original = app.os.open
    flags_seen = []
    def swapped(file_path, flags, *args, **kwargs):
        if file_path == selected:
            selected.unlink()
            os.mkfifo(selected)
            flags_seen.append(flags)
        return original(file_path, flags, *args, **kwargs)
    monkeypatch.setattr(app.os, "open", swapped)
    monkeypatch.setattr(app, "download_native", lambda **kwargs: pytest.fail("FIFO reached writer"))
    result = app.download_manifest_native(manifest_path=path, credential=b"\0" * 8)
    assert not result["ok"] and flags_seen[0] & os.O_NONBLOCK and flags_seen[0] & os.O_NOFOLLOW


@pytest.mark.parametrize("field,value", [("credential", b""), ("timeout_ms", 0), ("session_timeout_s", float("nan")), ("execute", 1)])
def test_bad_manifest_transaction_arguments_do_not_even_open_local_manifest(tmp_path, monkeypatch, field, value):
    monkeypatch.setattr(app, "_candidate_read", lambda *args, **kwargs: pytest.fail("bad args read files"))
    arguments = {"manifest_path": tmp_path / "absent", "credential": b"\0" * 8}
    arguments[field] = value
    result = app.download_manifest_native(**arguments)
    assert not result["ok"] and not result["device_access"] and result["stage"] == "arguments"


def test_manifest_local_snapshot_interrupt_keeps_not_sent_and_no_device_access(tmp_path, monkeypatch):
    def interrupted(*args, **kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr(app, "_candidate_read", interrupted)
    result = app.download_manifest_native(manifest_path=tmp_path / "unused", credential=b"\0" * 8)
    assert result["status"] == "interrupted" and result["request_outcome"] == "not_sent" and result["device_access"] is False


@pytest.mark.parametrize("action,state", [("run", 1), ("stop", 0)])
def test_control_cli_execute_calls_one_api_and_keeps_ack_without_state_readback(tmp_path, monkeypatch, capsys, action, state):
    calls = []
    def control(**kwargs):
        calls.append(kwargs)
        return {**app._report("gcm-set"), "ok": True, "status": "control-acknowledged",
                "device_access": True, "ack_u32": 0, "cpu_state_verified": False}
    monkeypatch.setattr(app, "set_gcm_native", control)
    assert app.main([action, "--execute", "--linux-device", "001:004", "--timeout-ms", "2222"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert calls == [{"state": state, "execute": True, "linux_device": "001:004", "timeout_ms": 2222, "session_timeout_s": 30.0}]
    assert report["status"] == "control-acknowledged" and report["cpu_state_verified"] is False


def test_unexpected_control_api_interrupt_keeps_possible_write_and_no_retry(monkeypatch, capsys):
    calls = []
    def interrupted(**kwargs):
        calls.append(kwargs)
        raise KeyboardInterrupt()
    monkeypatch.setattr(app, "set_gcm_native", interrupted)
    assert app.main(["run", "--execute", "--linux-device", "001:004"]) == 130
    report = json.loads(capsys.readouterr().out)
    assert len(calls) == 1 and report["plc_change_possible"] and report["device_access"] is None
    assert report["request_outcome"] == "unknown"
