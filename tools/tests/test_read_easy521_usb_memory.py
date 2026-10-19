"""Fake Windows runner, temporary leases, and static helper/profile checks only."""
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location("read_easy521_usb_memory",Path(__file__).parents[1]/"read_easy521_usb_memory.py")
reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
from plc_idf.lease import LeaseBusy

LIST="Connected:\nBUSID  VID:PID    DEVICE       STATE\n7-2    0471:a531  Easy521      Not shared\n"


def record(profile="revision",value=52102.0,sequence=4660):
    request=reader.codec.build_read_request(profile=profile,sequence=sequence)
    info=reader.codec.profile_info(profile)
    data=(struct.pack("<f",value) if info["iec_type"]=="REAL" else
          struct.pack("<h",int(value)) if info["iec_type"]=="INT" else bytes([int(bool(value))]))
    response=struct.pack("<H",22+len(data))+request[2:8]+b"\x02\0"+request[10:]+data
    return {"schema":reader.HELPER_SCHEMA,"scope":reader.HELPER_SCOPE,
            "library_sha256":reader.LIBRARY_SHA256,"read_profile":profile,
            "result":{"ok":True,"stage":"raw-frame-only","value_semantics":"candidate",
                      "write_calls":1,"bulk_write_rc":22,"configuration_rc":1,"claim_rc":0,
                      "release_rc":0,"close_rc":0,"descriptor_hex":"1201000202000040710431a5100301020001",
                      "request_hex":request.hex(),"response_hex":response.hex(),"read_rcs":[len(response)]}}


class FakeRunner:
    def __init__(self,payload=None,listing=LIST,timeout=False,helper_rc=0,lease_directory=None):
        self.payload=json.dumps(record()) if payload is None else payload
        self.listing,self.timeout,self.helper_rc,self.lease_directory=listing,timeout,helper_rc,lease_directory
        self.calls=[]
    def __call__(self,argv,*,timeout,cwd=None):
        self.calls.append(argv)
        assert timeout==25 and cwd is None
        if self.lease_directory:
            with pytest.raises(LeaseBusy):
                with reader.ResourceLease(reader.device_resource("7-2",reader.VIDPID),directory=self.lease_directory):pass
        if argv[-1]=="list":return SimpleNamespace(returncode=0,stdout=self.listing,stderr="")
        assert "-Execute" in argv and "-ReadProfile" in argv and "-Sequence" in argv
        assert "read_easy521_usb_memory.ps1" in argv[argv.index("-File")+1]
        if self.timeout:raise subprocess.TimeoutExpired(argv,timeout,output="partial raw evidence")
        return SimpleNamespace(returncode=self.helper_rc,stdout=self.payload,stderr="")


@pytest.fixture
def env(tmp_path,monkeypatch):
    helper=tmp_path/"read_easy521_usb_memory.ps1";helper.write_text("# fake, never run\n")
    monkeypatch.setattr(reader,"runtime_asset",lambda root,relative:helper)
    monkeypatch.setattr(reader.secrets,"randbits",lambda count:4660)
    return tmp_path/"leases",helper


def execute(env,fake,**kwargs):
    return reader.read_memory(execute=True,busid=kwargs.pop("busid","7-2"),runner=fake,lease_directory=env[0],**kwargs)


def test_preview_has_zero_external_calls_and_no_lease(monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail("preview performed an external/lease/random action")
    monkeypatch.setattr(reader,"runtime_asset",forbidden);monkeypatch.setattr(reader,"ResourceLease",forbidden)
    monkeypatch.setattr(reader.secrets,"randbits",forbidden)
    result=reader.read_memory(runner=forbidden)
    assert result["ok"] and result["status"]=="preview" and not result["device_access"]


def test_fixed_marker_candidate_binds_evidence_but_does_not_claim_hardware_calibration(env):
    fake=FakeRunner(lease_directory=env[0]);result=execute(env,fake)
    assert result["ok"] and result["status"]=="candidate-observed" and result["value_candidate"]==52102.0
    assert not result["hardware_verified"] and not result["application_execution_verified"]
    assert not result["scalar_interpretation_calibrated"] and not result["linux_native_usb"]
    assert result["symbol"]["allocation_csv_sha256"]==reader.codec.CSV_SHA256
    assert result["raw_transport"]["stdout"]==fake.payload and len(fake.calls)==2
    assert result["helper"]["sha256_after"]==result["helper"]["sha256"]


@pytest.mark.parametrize("profile,value",[("pcmd",0.125),("scan-counter",123),("confidence",246.0),("heartbeat",True),("m8013",False),("published-scan",100.0),("step",2.0)])
def test_each_scalar_candidate_profile_is_explicit_and_exact(env,profile,value):
    fake=FakeRunner(payload=json.dumps(record(profile,value)))
    result=execute(env,fake,profile=profile)
    assert result["ok"] and result["value_candidate"]==value
    assert fake.calls[1][-2:]==["-ReadProfile",profile]


@pytest.mark.parametrize("profile,data", [
    ("cur-error-count",struct.pack("<h",1)),
    ("cur-error-list",struct.pack("<h",1)+struct.pack("<hhii",5056,0,1,2)+b"\0"*492),
    ("cur-error-instances",b"\0"*168)])
def test_windows_bridge_diagnostic_profiles_preserve_exact_multichunk_raw_reply(env, profile, data):
    payload=record()
    request=reader.codec.build_read_request(profile=profile,sequence=4660)
    response=struct.pack("<H",22+len(data))+request[2:8]+b"\x02\0"+request[10:]+data
    payload["read_profile"]=profile
    payload["result"].update(request_hex=request.hex(),response_hex=response.hex(),
        read_rcs=[len(response[i:i+512]) for i in range(0,len(response),512)])
    fake=FakeRunner(payload=json.dumps(payload))
    result=execute(env,fake,profile=profile)
    assert result["ok"] and result["decoded"]["data_hex"]==data.hex() and len(fake.calls)==2
    assert result["symbol"]["allocation_gvt_sha256"] is None
    if profile=="cur-error-list":
        assert result["value_candidate"]["entries"][0]["error_code_u16"]==5056
        assert payload["result"]["read_rcs"]==[512,16]


@pytest.mark.parametrize("listing",[LIST.replace("7-2","6-4"),LIST+"7-3    0471:a531  Another PLC   Not shared\n",LIST.replace("Not shared","Attached"),LIST.replace("Not shared","Shared"),"bad table"])
def test_stale_ambiguous_or_owned_device_never_runs_helper(env,listing):
    fake=FakeRunner(listing=listing);result=execute(env,fake)
    assert not result["ok"] and not result["device_access"] and len(fake.calls)==1


@pytest.mark.parametrize("busid",[None,"",True,"7-2;exit"])
def test_explicit_valid_location_is_required(env,busid):
    fake=FakeRunner();assert not execute(env,fake,busid=busid)["ok"];assert not fake.calls


def test_busy_lease_prevents_even_enumeration(env):
    fake=FakeRunner()
    with reader.ResourceLease(reader.device_resource("7-2",reader.VIDPID),directory=env[0]):
        result=execute(env,fake)
    assert not result["ok"] and result["stage"]=="lease" and not fake.calls


def test_timeout_is_one_helper_attempt_and_unknown(env):
    fake=FakeRunner(timeout=True);result=execute(env,fake)
    assert not result["ok"] and result["request_outcome"]=="unknown" and len(fake.calls)==2
    assert result["raw_transport"]["timed_out"] and result["raw_transport"]["attempts"]==1
    assert result["raw_transport"]["stdout"]=="partial raw evidence"


@pytest.mark.parametrize("payload",["verified","{}","[]",'{"schema":1,"schema":2}',"{}\n{}",'{"value":NaN}'])
def test_raw_tokens_or_invalid_json_never_count_as_success(env,payload):
    fake=FakeRunner(payload=payload);result=execute(env,fake)
    assert not result["ok"] and result["raw_transport"]["stdout"]==payload and len(fake.calls)==2


@pytest.mark.parametrize("field,value",[("ok",1),("write_calls",True),("write_calls",2),("bulk_write_rc",21),("claim_rc",-1),("release_rc",-1),("close_rc",False),("configuration_rc",0),("read_rcs",[25]),("value_semantics","verified"),("request_hex","00")])
def test_false_transport_success_is_refused(env,field,value):
    data=record();data["result"][field]=value
    result=execute(env,FakeRunner(payload=json.dumps(data)))
    assert not result["ok"] and result["stage"]=="validate" and "value_candidate" not in result


@pytest.mark.parametrize("mutation",["sequence","address","bits","count","operation","error0","error1","nonfinite"])
def test_wrong_reply_fields_and_errors_are_failed_with_raw_evidence(env,mutation):
    data=record();frame=bytearray.fromhex(data["result"]["response_hex"])
    if mutation=="sequence":frame[4]^=1
    elif mutation=="address":frame[14]^=1
    elif mutation=="bits":frame[18]=16
    elif mutation=="count":frame[10]=2
    elif mutation=="operation":frame[8]=5
    elif mutation=="nonfinite":frame[22:]=struct.pack("<f",float("nan"))
    else:frame=bytearray.fromhex("0e000103341200000f00")+struct.pack("<I",int(mutation[-1]))
    data["result"].update(response_hex=frame.hex(),read_rcs=[len(frame)])
    fake=FakeRunner(payload=json.dumps(data));result=execute(env,fake)
    assert not result["ok"] and "value_candidate" not in result and len(fake.calls)==2
    assert result["raw_transport"]["stdout"]==fake.payload


def test_wrong_helper_profile_or_library_is_rejected(env):
    for key,value in (("read_profile","confidence"),("library_sha256","0"*64),("schema","other")):
        data=record();data[key]=value
        assert not execute(env,FakeRunner(payload=json.dumps(data)))["ok"]


def test_changed_helper_is_failed_unknown_after_raw_reply(env):
    fake=FakeRunner()
    def change(argv,**kwargs):
        result=fake(argv,**kwargs)
        if "-Execute" in argv:env[1].write_text("# concurrent change\n")
        return result
    result=execute(env,change)
    assert not result["ok"] and result["stage"]=="helper_integrity" and result["request_outcome"]=="unknown"
    assert result["raw_transport"]["stdout"]==fake.payload


def test_fresh_sequence_rejects_previous_calls_late_frame(env,monkeypatch):
    monkeypatch.setattr(reader.secrets,"randbits",lambda count:4661)
    data=record(sequence=4660)
    data["result"]["request_hex"]=reader.codec.build_read_request(profile="revision",sequence=4661).hex()
    assert not execute(env,FakeRunner(payload=json.dumps(data)))["ok"]


def test_nonzero_exit_cannot_be_overridden_by_success_json(env):
    assert not execute(env,FakeRunner(helper_rc=1))["ok"]


def test_main_is_one_json_for_preview_unknown_profile_and_missing_busid(capsys):
    for args,code in (([],0),(["--profile","Y0"],1),(["--execute"],1)):
        assert reader.main(args)==code
        captured=capsys.readouterr();assert isinstance(json.loads(captured.out),dict) and not captured.err


def test_helper_has_only_read_transport_apis_and_fixed_profiles():
    helper=(Path(__file__).parents[2]/"plc/tools/read_easy521_usb_memory.ps1").read_text()
    imports=set(re.findall(r"static extern (?:int|void|IntPtr) (usb_\w+)\(",helper))
    assert imports=={"usb_init","usb_find_busses","usb_find_devices","usb_get_busses","usb_open","usb_close","usb_get_descriptor","usb_control_msg","usb_strerror","usb_claim_interface","usb_release_interface","usb_bulk_write","usb_bulk_read"}
    entries=re.findall(r'case "([^"]+)": address=(0x[0-9a-fA-F]+); bits=(\d+); break;',helper)
    assert {name:(int(address,16),int(bits)) for name,address,bits in entries}=={
        name:(reader.codec.profile_info(name)["address_u32"],reader.codec.profile_info(name)["width_bits"]) for name in reader.PROFILES}
    previews=re.findall(r"'([^']+)'=@\((0x[0-9a-fA-F]+),(\d+)\)",helper)
    assert set(entries)==set(previews)
    assert helper.count("int sent=usb_bulk_write(")==1
    assert "usb_control_msg(handle,0x80,8,0,0,config,1,1000)" in helper
    assert helper.index("if (!$Execute)") < helper.index("Get-FileHash")
    assert "Get-Process -Name AutoShop" in helper
    assert "attempt<4" in helper and "ElapsedMilliseconds<2500" in helper
