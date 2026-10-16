"""串口（RTU）传输模式测试——无硬件，用假客户端 mock 验证管线。

覆盖点：
    1. --transport serial 时以正确参数构建 ModbusSerialClient（9600 8N1 默认可配）
    2. 串口轮询走与 TCP 完全相同的读路径/解码/JSONL（复用逻辑）
    3. 严格只读：对假客户端的每一次调用都是读方法，无任何写调用
"""

import json

import e521_monitor
from e521_regs import encode_quantities

SAMPLE_VALUES = {
    "Vpu": 0.91, "Pline": 0.42, "Qline": 0.20, "loading": 0.46,
    "SOC": 0.75, "Pess": 0.0, "Qess": 0.0, "Pcmd": 0.0, "Qcmd": 0.35,
}


# =====================【假客户端】=====================

class FakeResult:
    """模拟 pymodbus 响应对象：isError() + registers/bits。"""

    def __init__(self, registers=None, bits=None, error=False):
        self.registers = registers or []
        self.bits = bits or []
        self._error = error

    def isError(self):
        return self._error


class FakeSerialClient:
    """记录每次调用的假 ModbusSerialClient（构造参数可断言）。"""

    instances = []

    def __init__(self, port, *, framer=None, baudrate=9600, bytesize=8,
                 parity="N", stopbits=1, timeout=3.0):
        self.init_kwargs = dict(port=port, framer=framer, baudrate=baudrate,
                                bytesize=bytesize, parity=parity,
                                stopbits=stopbits, timeout=timeout)
        self.calls = []          # (方法名, 参数)
        self.connected = False
        FakeSerialClient.instances.append(self)

    def connect(self):
        self.calls.append(("connect", {}))
        self.connected = True
        return True

    def close(self):
        self.calls.append(("close", {}))

    # ---- 只读方法（本测试断言 fake 收到的调用不超出这一组）----
    def read_holding_registers(self, address=0, count=1, device_id=1):
        self.calls.append(("read_holding_registers", {"address": address,
                                                      "count": count,
                                                      "device_id": device_id}))
        words = encode_quantities(SAMPLE_VALUES, "abcd")
        return FakeResult(registers=(words + [1])[:count])

    def read_coils(self, address=0, count=1, device_id=1):
        self.calls.append(("read_coils", {"address": address, "count": count,
                                          "device_id": device_id}))
        return FakeResult(bits=[True, True, False, False, False, False, False, False])

    def read_discrete_inputs(self, address=0, count=1, device_id=1):
        self.calls.append(("read_discrete_inputs", {"address": address,
                                                    "count": count,
                                                    "device_id": device_id}))
        return FakeResult(bits=[True, True, False, False, False, False, False, False])

    # ---- 写方法：一旦被调用立即让测试失败 ----
    def write_register(self, *a, **k):
        raise AssertionError("监测客户端严禁出现写调用（FC06）")

    def write_registers(self, *a, **k):
        raise AssertionError("监测客户端严禁出现写调用（FC16）")

    def write_coil(self, *a, **k):
        raise AssertionError("监测客户端严禁出现写调用（FC05）")

    def write_coils(self, *a, **k):
        raise AssertionError("监测客户端严禁出现写调用（FC15）")


# =====================【客户端构建参数】=====================

def test_build_serial_client_default_params(monkeypatch):
    """默认 9600 8N1：与需求一致。"""
    args = e521_monitor.parse_args(
        ["--transport", "serial", "--device", "/dev/ttyUSB0", "--once"])
    monkeypatch.setattr(e521_monitor, "ModbusSerialClient", FakeSerialClient)
    client, target = e521_monitor.build_client(args)
    assert client.init_kwargs["port"] == "/dev/ttyUSB0"
    assert client.init_kwargs["baudrate"] == 9600
    assert client.init_kwargs["bytesize"] == 8
    assert client.init_kwargs["parity"] == "N"
    assert client.init_kwargs["stopbits"] == 1
    assert target.startswith("serial:///dev/ttyUSB0@9600")


def test_build_serial_client_custom_params(monkeypatch):
    """自定义波特率/校验：参数原样透传。"""
    args = e521_monitor.parse_args(["--transport", "serial", "--device",
                                    "/dev/ttyUSB0", "--baud", "19200",
                                    "--parity", "E", "--stopbits", "2", "--once"])
    monkeypatch.setattr(e521_monitor, "ModbusSerialClient", FakeSerialClient)
    client, _ = e521_monitor.build_client(args)
    assert client.init_kwargs["baudrate"] == 19200
    assert client.init_kwargs["parity"] == "E"
    assert client.init_kwargs["stopbits"] == 2


# =====================【串口轮询管线 + 严格只读】=====================

def test_serial_poll_pipeline_readonly_and_jsonl(monkeypatch, tmp_path, capsys):
    """串口模式一次完整轮询：解码正确、JSONL 落盘、调用集不含任何写。"""
    FakeSerialClient.instances.clear()
    jsonl_path = tmp_path / "serial.jsonl"
    args = e521_monitor.parse_args([
        "--transport", "serial", "--device", "/dev/ttyUSB0",
        "--once", "--calibrate", "--with-bits",
        "--jsonl", str(jsonl_path),
    ])
    monkeypatch.setattr(e521_monitor, "ModbusSerialClient", FakeSerialClient)

    rc = e521_monitor.main([
        "--transport", "serial", "--device", "/dev/ttyUSB0",
        "--once", "--calibrate", "--with-bits",
        "--jsonl", str(jsonl_path),
    ])
    assert rc == 0

    out = capsys.readouterr().out
    assert "串口" in out or "serial" in out.lower()
    assert "ABCD" in out                      # 校准结论打印

    # JSONL 行解码正确（0.91 低压帧：Qcmd≈0.35）
    rows = [json.loads(line) for line in
            jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["transport"] == "serial"
    assert row["word_order"] == "abcd"
    import struct
    assert struct.pack(">f", row["values"]["Vpu"]) == struct.pack(">f", 0.91)
    assert 0.30 <= row["values"]["Qcmd"] <= 0.40

    # 严格只读：全部调用必须是读方法
    client = FakeSerialClient.instances[-1]
    names = [c[0] for c in client.calls]
    assert names, "至少应有一次读调用"
    assert all(n.startswith("read_") or n in ("connect", "close")
               for n in names), f"出现非读调用: {names}"
    assert "read_holding_registers" in names   # FC03 必须在


def test_serial_connection_failure_exit_code(monkeypatch, capsys):
    """串口打开失败：--once 返回码 1，错误信息中文可读。"""
    class FailClient(FakeSerialClient):
        def connect(self):
            self.calls.append(("connect", {}))
            raise OSError("串口不存在/被占用: /dev/ttyUSB0")

    monkeypatch.setattr(e521_monitor, "ModbusSerialClient", FailClient)
    rc = e521_monitor.main(["--transport", "serial", "--device",
                            "/dev/ttyUSB0", "--once"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "连接失败" in err or "无法连接" in err
